"""The chat tab's code branch (docs/chat-deep-search-creative-sandbox.md §5).

    code_plan     the LM Studio 27B writes the files and the command (think: thinking tokens on)
    write_files   files go to artifacts/code/<run_id>/. fast ends here: files and intent, never a run. think
                  checks Docker (not running: the files and the reason, no run)
    confirm_run   interrupt with the HITL card (files and sizes, argv, image, network, timeout, memory).
                  approve -> sandbox_exec; edit -> the command, network or files change and the card comes back;
                  reject -> no run, the files stay
    sandbox_exec  sandbox.run (the only place that starts a container), after the image tab is idle, under
                  ``sandbox_lock`` (not the job lock: the container is capped at 2 GB and the 27B may stay)
    observe       success -> answer. A failure goes back to the 27B for a fix and to a new confirmation, at most
                  2 runs in total (first + one fix)

When the control loop called the code branch, every end goes to controller_record instead of END; the approval
card stays (the loop never skips it).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import interrupt

from furry_agent import coding, sandbox
from furry_agent.chat_common import (CONTROL_RECORD, ChatState, _ask, _cleanup, _conf, _decision, _edited_args, _fail,
                                     _final, _held, _hitl, _history, _image_tab_busy, _is_think, _lmstudio, _lock,
                                     _progress, _prompt, _settings, controlled, end_or_record, log)
from furry_agent.config import REPO_ROOT, ChatSettings
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LLMError

RUN_ACTION = "run_code"
CODE_TOKENS = 3500
sandbox_lock = asyncio.Lock()


def _runner(config: RunnableConfig | None):
    """sandbox's docker CLI runner; tests replace it (no Docker needed)."""
    return _conf(config).get("sandbox_runner")


def _rel(path: Path) -> str:
    try:
        return Path(path).relative_to(REPO_ROOT).as_posix()  # no resolve(): it blocks the event loop
    except ValueError:
        return str(path)


def _task(code: dict, extra: list[dict] | None = None) -> dict:
    steps = []
    if code.get("spec"):
        steps.append({"title": "仕様", "body": code["spec"]})
    if code.get("written"):
        steps.append({"title": "ファイル", "body": "\n".join(f"- {f['path']}（{f['bytes']} B）" for f in code["written"])})
    if code.get("command"):
        steps.append({"title": "コマンド", "body": coding.command_text(code["command"])})
    for run in code.get("runs") or []:
        status = "時間切れ" if run.get("timed_out") else f"終了コード {run.get('exit_code')}"
        steps.append({"title": f"実行 {run['round']}（{run.get('step', 'run')}）", "body": f"{status}、{run.get('seconds', 0):.1f} 秒"})
    if code.get("problems"):
        steps.append({"title": "注意", "body": "\n".join(f"- {p}" for p in code["problems"])})
    return {"kind": "code", "profile": code.get("profile"), "image": code.get("image"),
            "artifact_dir": code.get("artifact_dir"), "round": code.get("round", 0), "steps": [*steps, *(extra or [])]}


async def _generate(state: ChatState, config: RunnableConfig, settings: ChatSettings, code: dict,
                    fix: bool) -> tuple[coding.CodePlan, list[dict]]:
    """Ask the 27B for the files (a first plan or a fix of the failed run). Holds the job lock only for the call."""
    lmstudio = _lmstudio(config, settings)
    profile = sandbox.PROFILES[code["profile"]]
    system = (await _prompt(settings, "system_code_plan.txt")).replace("{image}", profile.image).replace(
        "{language}", profile.language.capitalize()).replace(
        "{python_cmd}", coding.command_text(list(sandbox.PYTHON.default_command))).replace(
        "{rust_cmd}", coding.command_text(list(sandbox.RUST.default_command)))
    if fix:
        user = coding.fix_input(code["request"], code)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    else:
        messages = [{"role": "system", "content": system}, *_history(state, 6)]
        if controlled(state):
            # The control loop's request for this tool (the thread's last turn may be an earlier tool's output).
            messages.append({"role": "user", "content": code["request"]})
    token = state.get("lock_token")
    try:
        token = await _lock(state, config, settings)
        async with _held(token):
            reply, thoughts = await _ask(state, settings, lmstudio, messages, base=CODE_TOKENS, answer_min=1200,
                                         temperature=0.2, stage="修正" if fix else "コード")
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    finally:
        # The container never needs the job lock (§5.5): give it back right after the model call.
        await _cleanup(token)
    plan = coding.parse_plan(reply.content, profile, code.get("files") if fix else None)
    return plan, thoughts


async def code_plan(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    request = state["route"]["text"]
    profile = sandbox.profile_for(request)
    run_id = sandbox.safe_run_id(state["progress_id"].removeprefix("progress-"))
    code: dict[str, Any] = {
        "request": request, "profile": profile.name, "image": profile.image, "run_id": run_id,
        "artifact_dir": _rel(sandbox.run_dir_for(settings.code_dir, run_id)),
        "files": [], "command": [], "setup": [], "spec": "", "round": 0, "last_exit": None,
        "stdout_tail": "", "stderr_tail": "", "timed_out": False, "approved": False,
        # Dependencies (network for the setup step) only when the user asked for them in this turn (§5.3).
        "network_requested": coding.wants_network(request), "network": False, "runs": [], "problems": []}
    try:
        plan, thoughts = await _generate(state, config, settings, code, fix=False)
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        await _cleanup(None, _lmstudio(config, settings), unload=True)
        return _fail(state, f"LM Studio に接続できないか、時間切れです（{exc}）", "code")
    if not plan.files:
        return {**_fail(state, "コードを取り出せませんでした（" + "、".join(plan.problems) + "）", "code"), "code": code}
    code.update({"spec": plan.spec, "files": plan.files, "command": plan.command, "setup": plan.setup,
                 "problems": plan.problems})
    log.info("code plan profile=%s files=%d setup=%s network_requested=%s", profile.name, len(plan.files),
             bool(plan.setup), code["network_requested"])
    return {"code": code, "lock_token": None, "thinking": thoughts,
            "messages": [_progress(state, "ファイルを書き出しています…", task=_task(code))]}


def _after_plan(state: ChatState) -> str:
    return end_or_record(state) if state.get("error") else "write_files"


def _answer(code: dict, head: str) -> str:
    parts = [head]
    if code.get("spec"):
        parts.append(f"**仕様**: {code['spec']}")
    parts.append(f"**ファイル**（`{code['artifact_dir']}`）\n\n{coding.files_markdown(code['files'])}")
    parts.append(f"**コマンド**: `{coding.command_text(code['command'])}`")
    if code.get("setup"):
        parts.append(f"**依存の取得**: `{coding.command_text(code['setup'])}`")
    return "\n\n".join(parts)


async def write_files(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    code = dict(state["code"])
    run_dir = sandbox.run_dir_for(settings.code_dir, code["run_id"])
    try:
        code["written"] = await asyncio.to_thread(sandbox.write_files, run_dir, code["files"])
    except (sandbox.SandboxError, OSError) as exc:
        return {**_fail(state, exc, "code"), "code": code}
    log.info("code files written dir=%s files=%d", code["artifact_dir"], len(code["written"]))
    if not _is_think(state):
        text = _answer(code, "コードを書きました。速いモードでは実行しません（「思考」で送ると、承認のあと Docker コンテナで実行します）。")
        return {"code": code, "messages": [_final(state, text, task=_task(code))]}
    ok, reason = await sandbox.docker_status(settings.docker_exe, _runner(config))
    code["start_desktop"] = False
    if not ok and await sandbox.desktop_cli(settings.docker_exe, _runner(config)):
        # Installed but stopped: started after the approval, only for the run (sandbox_exec).
        ok, code["start_desktop"] = True, True
    elif ok and not await sandbox.image_present(code["image"], settings.docker_exe, _runner(config)):
        ok, reason = False, (f"コンテナイメージ {code['image']} がありません（scripts\\setup-sandbox.ps1"
                             + (" -Rust" if code["profile"] == "rust" else "") + " で取得してください）")
    if not ok:
        log.info("sandbox unavailable: %s", reason)
        code["skipped"] = reason
        text = _answer(code, f"コードを書きましたが、実行は省きました: {reason}")
        return {"code": code, "messages": [_final(state, text, task=_task(code))]}
    return {"code": code, "messages": [_progress(state, "コンテナの承認待ちです。内容を確認して承認してください。", task=_task(code))]}


def _after_files(state: ChatState) -> str:
    code = state.get("code") or {}
    if state.get("error") or not _is_think(state) or code.get("skipped"):
        return end_or_record(state)
    return "confirm_run"


def _card(code: dict) -> tuple[dict, str]:
    args: dict[str, Any] = {
        "command": coding.command_text(code["command"]),
        "setup": coding.command_text(code["setup"]) if code.get("setup") else "",
        "network": "setup" if code.get("network_requested") and code.get("setup") else "none",
        "image": code["image"], "timeout_s": sandbox.TIMEOUT_S, "memory": sandbox.MEMORY,
        "files": ", ".join(f"{f['path']} ({f['bytes']} B)" for f in code.get("written") or []),
    }
    for f in code["files"]:
        args[f"file:{f['path']}"] = f["content"]
    description = (
        f"Docker コンテナ（{code['image']}）で次のコマンドを実行します。承認するまで実行しません。\n"
        f"- コマンド: {args['command']}\n"
        + (f"- 依存の取得（先に別のコンテナで実行）: {args['setup']}\n" if args["setup"] else "")
        + f"- ネットワーク: {'依存の取得のときだけ使う' if args['network'] == 'setup' else 'なし'}\n"
        f"- 上限: {sandbox.TIMEOUT_S} 秒、メモリ {sandbox.MEMORY}、CPU {sandbox.CPUS}、プロセス {sandbox.PIDS_LIMIT}\n"
        f"- マウント: {code['artifact_dir']} → /work のみ（読み取り専用ルート、非 root、権限なし）\n"
        + ("- Docker Desktop: 停止中。承認後に起動し（30 秒〜数分）、実行が終わったら止めます\n"
           if code.get("start_desktop") else "")
        + f"- ファイル: {args['files']}\n"
        "変えるときは command / setup / network（none か setup）/ file:<パス> を書き換えて送信（編集）、"
        "やめるときは却下してください。")
    return args, description


async def confirm_run(state: ChatState, config: RunnableConfig) -> dict:
    """Nothing runs before ``approve`` here (§5.4)."""
    settings = _settings(config)
    code = dict(state["code"])
    args, description = _card(code)
    response = interrupt(_hitl(RUN_ACTION, args, description))
    decision = _decision(response)
    kind = decision.get("type")
    if kind == "approve":
        code["approved"] = True
        code["network"] = args["network"] == "setup"
        log.info("code run approved run_id=%s round=%d", code["run_id"], code["round"] + 1)
        return {"code": code, "messages": [_progress(state, "コンテナを実行しています…", task=_task(code))]}
    if kind == "edit":
        edited = _edited_args(decision)
        profile = sandbox.PROFILES[code["profile"]]
        problems = []
        try:
            if "command" in edited:
                code["command"] = sandbox.parse_command(str(edited["command"]), profile)
            if "setup" in edited:
                code["setup"] = (sandbox.parse_command(str(edited["setup"]), profile, setup=True)
                                 if str(edited["setup"]).strip() else [])
            if "network" in edited:
                value = str(edited["network"]).strip().lower()
                if value not in ("none", "setup"):
                    raise sandbox.SandboxError("network は none か setup です")
                code["network_requested"] = value == "setup"
            files = {f["path"]: dict(f) for f in code["files"]}
            for key, value in edited.items():
                if key.startswith("file:"):
                    path = sandbox.safe_relpath(key[5:])
                    files[path] = {"path": path, "content": str(value), "language": coding.language_of(path)}
            code["files"] = list(files.values())
            run_dir = sandbox.run_dir_for(settings.code_dir, code["run_id"])
            code["written"] = await asyncio.to_thread(sandbox.write_files, run_dir, code["files"])
        except sandbox.SandboxError as exc:
            problems.append(str(exc))
        code.update({"approved": False, "problems": problems})
        note = "変更を反映しました。もう一度確認してください。" if not problems else f"変更を反映できませんでした: {problems[0]}"
        return {"code": code, "messages": [_progress(state, note, task=_task(code))]}
    code["approved"] = False
    log.info("code run rejected run_id=%s", code["run_id"])
    text = _answer(code, "実行しませんでした。ファイルはそのまま残しています。")
    return {"code": code, "error": "rejected", "messages": [_final(state, text, task=_task(code))]}


def _after_confirm(state: ChatState) -> str:
    if state.get("error"):
        return end_or_record(state)
    return "sandbox_exec" if (state.get("code") or {}).get("approved") else "confirm_run"


async def _wait_for_image_tab(config: RunnableConfig, settings: ChatSettings) -> bool:
    """The container waits while the image tab generates (ComfyUI owns the memory then). The image run is waited
    for as long as it works (it has its own idle timeout); SANDBOX_WAIT_S, when set, bounds the wait."""
    deadline = time.monotonic() + settings.sandbox_wait_s if settings.sandbox_wait_s is not None else None
    while job_lock.holder == "image" or await _image_tab_busy(config, settings):
        if deadline is not None and time.monotonic() > deadline:
            return False
        await asyncio.sleep(2.0)
    return True


async def sandbox_exec(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    code = dict(state["code"])
    if code.get("approved") is not True:  # never reached without approval; refuse anyway
        return {**_fail(state, "承認されていないため実行しません", "sandbox"), "code": code}
    profile = sandbox.PROFILES[code["profile"]]
    run_dir = sandbox.run_dir_for(settings.code_dir, code["run_id"])
    runner = _runner(config)
    if not await _wait_for_image_tab(config, settings):
        code["skipped"] = "画像タブの生成が終わらないため実行を見送りました"
        return {"code": code, "messages": [_final(state, _answer(code, code["skipped"]), task=_task(code))]}
    before = {f["path"] for f in code.get("written") or []}
    runs = list(code.get("runs") or [])
    round_no = code.get("round", 0) + 1
    async with sandbox_lock:
        started = False
        if code.get("start_desktop") and not (await sandbox.docker_status(settings.docker_exe, runner))[0]:
            # Docker Desktop was stopped (its VM holds ~1.5 GB the 27B and ComfyUI need on this machine): start it
            # for this approved run only and stop it again afterwards.
            started = True
            ok, reason = await sandbox.start_desktop(settings.docker_exe, runner)
            if ok and not await sandbox.image_present(code["image"], settings.docker_exe, runner):
                ok, reason = False, f"コンテナイメージ {code['image']} がありません（scripts\\setup-sandbox.ps1 で取得してください）"
            if not ok:
                await sandbox.stop_desktop(settings.docker_exe, runner)
                code.update({"skipped": reason, "approved": False})
                return {"code": code, "messages": [_final(state, _answer(code, f"実行できませんでした: {reason}"),
                                                          task=_task(code))]}
        try:
            setup = None
            if code.get("setup") and code.get("network"):
                setup = await sandbox.run(approved=True, docker=settings.docker_exe, run_dir=run_dir,
                                          root=settings.code_dir, argv=code["setup"], profile=profile,
                                          user=settings.sandbox_user, network=True, step="setup",
                                          name=sandbox.container_name(code["run_id"], round_no, "setup"),
                                          runner=runner)
                runs.append({"round": round_no, "step": "setup", "exit_code": setup.exit_code,
                             "timed_out": setup.timed_out, "seconds": setup.seconds})
            if setup is not None and not setup.ok:
                result = setup
            else:
                result = await sandbox.run(approved=True, docker=settings.docker_exe, run_dir=run_dir,
                                           root=settings.code_dir, argv=code["command"], profile=profile,
                                           user=settings.sandbox_user, network=False, step="run",
                                           name=sandbox.container_name(code["run_id"], round_no), runner=runner)
                runs.append({"round": round_no, "step": "run", "exit_code": result.exit_code,
                             "timed_out": result.timed_out, "seconds": result.seconds})
        finally:
            if started:
                await asyncio.shield(sandbox.stop_desktop(settings.docker_exe, runner))
    outputs = await asyncio.to_thread(sandbox.list_outputs, run_dir, before)
    # One approval is one run: a fix needs a new approval.
    code.update({"round": round_no, "last_exit": result.exit_code, "stdout_tail": result.stdout_tail,
                 "stderr_tail": result.stderr_tail + (f"\n{result.error}" if result.error else ""),
                 "timed_out": result.timed_out, "approved": False, "runs": runs, "outputs": outputs,
                 "last_ok": result.ok, "last_step": result.step})
    return {"code": code, "messages": [_progress(state, "実行結果を確認しています…", task=_task(code))]}


def _after_exec(state: ChatState) -> str:
    return end_or_record(state) if state.get("error") or (state.get("code") or {}).get("skipped") else "observe"


def _result_text(code: dict) -> str:
    status = "時間切れ（60 秒で停止）" if code.get("timed_out") else f"終了コード {code.get('last_exit')}"
    parts = [f"**実行結果**（{code.get('round')} 回目、{status}）"]
    if code.get("stdout_tail"):
        parts.append(f"stdout（末尾）:\n```text\n{code['stdout_tail'].rstrip()[-4000:]}\n```")
    if code.get("stderr_tail", "").strip():
        parts.append(f"stderr（末尾）:\n```text\n{code['stderr_tail'].rstrip()[-4000:]}\n```")
    if code.get("outputs"):
        parts.append("作られたファイル: " + "、".join(f"`{o['path']}`" for o in code["outputs"]))
    return "\n\n".join(parts)


async def observe(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    code = dict(state["code"])
    code["fixing"] = False
    if code.get("last_ok"):
        text = _answer(code, "コードを実行しました。") + "\n\n" + _result_text(code)
        return {"code": code, "messages": [_final(state, text, task=_task(code))]}
    if code.get("round", 0) >= sandbox.MAX_RUNS:
        text = _answer(code, f"{sandbox.MAX_RUNS} 回実行しましたが成功しませんでした。") + "\n\n" + _result_text(code)
        return {"code": code, "messages": [_final(state, text, task=_task(code))]}
    try:
        plan, thoughts = await _generate(state, config, settings, code, fix=True)
    except JobLockBusy as exc:
        return {**_fail(state, exc, "lock"), "code": code}
    except (LLMError, OSError) as exc:
        text = _answer(code, f"実行に失敗し、修正もできませんでした（{exc}）。") + "\n\n" + _result_text(code)
        return {"code": code, "messages": [_final(state, text, task=_task(code))]}
    code.update({"files": plan.files, "command": plan.command, "setup": plan.setup or code.get("setup") or [],
                 "problems": plan.problems, "spec": plan.spec or code.get("spec", ""), "fixing": True})
    log.info("code fix generated run_id=%s files=%d", code["run_id"], len(plan.files))
    text = f"{_result_text(code)}\n\n失敗したので修正しました。もう一度確認してください（実行は最大 {sandbox.MAX_RUNS} 回）。"
    return {"code": code, "thinking": thoughts, "messages": [_progress(state, text, task=_task(code))]}


def _after_observe(state: ChatState) -> str:
    code = state.get("code") or {}
    if state.get("error") or code.get("last_ok") or code.get("round", 0) >= sandbox.MAX_RUNS:
        return end_or_record(state)
    return "write_files" if code.get("fixing") and code.get("files") else end_or_record(state)


def add_nodes(builder: Any) -> None:
    builder.add_node("code_plan", code_plan)
    builder.add_node("write_files", write_files)
    builder.add_node("confirm_run", confirm_run)
    builder.add_node("sandbox_exec", sandbox_exec)
    builder.add_node("observe", observe)
    builder.add_conditional_edges("code_plan", _after_plan, ["write_files", CONTROL_RECORD, END])
    builder.add_conditional_edges("write_files", _after_files, ["confirm_run", CONTROL_RECORD, END])
    builder.add_conditional_edges("confirm_run", _after_confirm, ["sandbox_exec", "confirm_run", CONTROL_RECORD, END])
    builder.add_conditional_edges("sandbox_exec", _after_exec, ["observe", CONTROL_RECORD, END])
    builder.add_conditional_edges("observe", _after_observe, ["write_files", CONTROL_RECORD, END])
