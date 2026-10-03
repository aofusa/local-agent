# Allow inbound TCP 2024 (LangGraph) and 3000 (agent-chat-ui) on the Private profile only.
# ComfyUI (8188) and LM Studio (1234) stay loopback-only and are not opened.
# Needs an elevated PowerShell.
$ErrorActionPreference = "Stop"
foreach ($rule in @(
    @{ Name = "local-agent LangGraph 2024"; Port = 2024 },
    @{ Name = "local-agent agent-chat-ui 3000"; Port = 3000 }
)) {
    Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Protocol TCP -LocalPort $rule.Port `
        -Action Allow -Profile Private | Out-Null
    Write-Host "allowed TCP $($rule.Port) (Private): $($rule.Name)"
}
