"""/docs path policy and chunking (docs/local-doc-mapreduce-design.md §5.2, §5.3, §5.8). No model."""

import os
import sys

import pytest

from furry_agent import doc_chunk
from furry_agent.doc_resolve import DocError, deny_reason, inside, parse_roots, read_text, resolve


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    (root / "docs" / "deep" / "a" / "b" / "c" / "d").mkdir(parents=True)
    (root / "README.md").write_text("# 概要\n本文", encoding="utf-8")
    (root / "notes.txt").write_text("メモ", encoding="utf-8")
    (root / "main.py").write_text("print(1)", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / ".env.local").write_text("SECRET=1", encoding="utf-8")
    (root / "server.pem").write_text("key", encoding="utf-8")
    (root / "credentials.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "design.md").write_text("# 設計\n内容", encoding="utf-8")
    (root / "docs" / "deep" / "a" / "b" / "level4.md").write_text("4", encoding="utf-8")
    (root / "docs" / "deep" / "a" / "b" / "c" / "level5.md").write_text("5", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config.txt").write_text("x", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.json").write_text("{}", encoding="utf-8")
    (root / "outputs").mkdir()
    (root / "outputs" / "list.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret_plan.md").write_text("外の文書", encoding="utf-8")
    (outside / "plain.md").write_text("外の文書", encoding="utf-8")
    (tmp_path / "root2").mkdir()
    return root, outside


def _rels(target):
    return [f.rel for f in target.files]


def test_unset_roots_turn_the_feature_off(tree):
    with pytest.raises(DocError) as err:
        resolve("README.md", [])
    assert err.value.code == "unset" and "LOCAL_DOC_ROOTS が未設定です" in str(err.value)


def test_parse_roots_keeps_absolute_existing_dirs_only(tree, tmp_path):
    root, _ = tree
    roots = parse_roots(f"{root}, relative/dir ,{tmp_path / 'missing'},")
    assert roots == [root, tmp_path / "missing"]  # existence is checked per request (real_roots)


def test_directory_lists_text_files_breadth_first_and_denies_secrets(tree):
    root, _ = tree
    target = resolve(str(root), [root])
    rels = _rels(target)
    assert rels[:2] == ["notes.txt", "README.md"] or rels[:2] == ["README.md", "notes.txt"]
    assert "docs/design.md" in rels and "docs/deep/a/b/level4.md" in rels
    assert "docs/deep/a/b/c/level5.md" not in rels  # depth 5 is beyond DOC_MAX_DEPTH
    denied = {d["rel"]: d["reason"] for d in target.denied}
    assert denied[".env"] == ".env" and denied[".env.local"] == ".env.*"
    assert denied["server.pem"] == "*.pem" and denied["credentials.json"] == "credentials*"
    assert ".git/" in denied and "node_modules/" in denied and "outputs/" in denied
    assert not any(r.startswith((".git/", "node_modules/", "outputs/")) for r in rels)
    assert target.skipped == 1  # main.py: counted, not listed


def test_relative_path_is_resolved_against_the_roots(tree, tmp_path):
    root, _ = tree
    target = resolve("docs/design.md", [tmp_path / "root2", root])
    assert _rels(target) == ["docs/design.md"] and target.single


def test_outside_and_prefix_trick_are_refused(tree, tmp_path):
    root, outside = tree
    with pytest.raises(DocError) as err:
        resolve(str(outside / "plain.md"), [root])
    assert err.value.code == "outside" and "許可したディレクトリの外です" in str(err.value)
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    (sibling / "x.md").write_text("x", encoding="utf-8")
    assert not inside(sibling / "x.md", root)  # a string prefix is not enough
    with pytest.raises(DocError):
        resolve(str(sibling / "x.md"), [root])


@pytest.mark.parametrize("raw", ["../outside/plain.md", "docs/../../outside/plain.md", "README.md:secret",
                                 "\\\\?\\C:\\Windows\\win.ini", "C:README.md", "\\README.md"])
def test_traversal_streams_and_odd_paths_are_refused(tree, raw):
    root, _ = tree
    with pytest.raises(DocError) as err:
        resolve(raw, [root])
    assert err.value.code == "bad_path"


def test_denied_target_shows_the_name_only(tree):
    root, _ = tree
    with pytest.raises(DocError) as err:
        resolve(".env", [root])
    assert err.value.code == "denied" and "SECRET" not in str(err.value)
    assert err.value.denied == [{"rel": ".env", "reason": ".env"}]


def test_denied_names_case_insensitive_anywhere():
    assert deny_reason(["Docs", "Node_Modules", "a.md"]) == "node_modules"
    assert deny_reason(["MODEL.SafeTensors"]) == "*.safetensors"
    assert deny_reason(["secrets.md"]) == "secret*"
    assert deny_reason(["NUL.txt"]) == "デバイス名"
    assert deny_reason(["docs", "readme.md"]) is None


def test_file_limit_drops_the_rest(tree):
    root, _ = tree
    target = resolve(str(root), [root], max_files=2)
    assert len(target.files) == 2 and target.dropped >= 1


def test_wrong_extension_alone_is_empty(tree):
    root, _ = tree
    with pytest.raises(DocError) as err:
        resolve("main.py", [root])
    assert err.value.code == "empty"


def _link(src, dst, directory):
    try:
        os.symlink(dst, src, target_is_directory=directory)
        return True
    except (OSError, NotImplementedError):
        return False


def test_symlink_out_of_the_root_is_refused(tree):
    root, outside = tree
    if not _link(root / "link.md", outside / "plain.md", False):
        pytest.skip("symlinks need developer mode or admin on Windows")
    with pytest.raises(DocError) as err:
        resolve("link.md", [root])
    assert err.value.code == "outside"
    target = resolve(str(root), [root])
    assert {"rel": "link.md", "reason": "リンク先が許可ルートの外"} in target.denied


def test_junction_to_outside_is_not_followed(tree):
    root, outside = tree
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    import _winapi

    _winapi.CreateJunction(str(outside), str(root / "jn"))
    target = resolve(str(root), [root])
    assert not any(f.rel.startswith("jn/") for f in target.files)
    with pytest.raises(DocError) as err:
        resolve("jn/plain.md", [root])
    assert err.value.code == "outside"


def test_read_text_truncates_and_skips_binary(tree, tmp_path):
    root, _ = tree
    big = root / "big.txt"
    big.write_text("あ" * 1000, encoding="utf-8")
    target = resolve("big.txt", [root])
    text, truncated = read_text(target.files[0], 300)
    assert truncated and len(text.encode("utf-8")) <= 300 + 3
    (root / "bin.txt").write_bytes(b"abc\x00def")
    text, _ = read_text(resolve("bin.txt", [root]).files[0], 1000)
    assert text == ""


# --- chunking --------------------------------------------------------------------------------------------------------


def test_markdown_is_cut_at_headings_and_long_sections_keep_the_heading():
    body = "前置き\n# 第一\n" + "あ" * 50 + "\n## 第二\n" + ("い" * 99 + "\n") * 60 + "### 第三\nう\n```\n# not a heading\n```\n"
    chunks = doc_chunk.chunk_file(body, "docs/x.md", 2, size=1000, overlap=100)
    headings = [c.heading for c in chunks]
    assert headings[0] == "" and headings[1] == "第一"
    assert headings.count("第二") >= 5 and headings[-1] == "第三"
    assert all(c.chars <= 1000 for c in chunks)
    assert [c.id for c in chunks[:2]] == ["f2-c1", "f2-c2"]
    assert chunks[1].locator == "docs/x.md#第一" and chunks[0].locator.startswith("docs/x.md#L1-")
    assert "# not a heading" in chunks[-1].text
    second = [c for c in chunks if c.heading == "第二"]
    assert second[0].text[-50:] in second[1].text  # overlap


def test_plain_text_and_json_are_cut_by_characters():
    text = "\n".join(f'{{"k{i}": "{"v" * 40}"}}' for i in range(200))
    chunks = doc_chunk.chunk_file(text, "data.json", 1, size=3000, overlap=200)
    assert len(chunks) > 1 and all(c.heading == "" for c in chunks)
    assert chunks[0].locator.startswith("data.json#L1-L")
    assert all(c.chars <= 3000 for c in chunks)


def test_outline_has_ids_and_headings_without_body():
    chunks = doc_chunk.chunk_file("# A\n本文の秘密\n# B\nx", "a.md", 1)
    text = doc_chunk.outline([c.meta() for c in chunks], [{"file_no": 1, "rel": "a.md", "size": 30}])
    assert "f1-c1 A" in text and "f1-c2 B" in text and "本文の秘密" not in text
