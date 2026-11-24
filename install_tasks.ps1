# Registra duas tarefas sem console. Execute uma vez após configurar/testar.
# Não armazena senha do Windows. Funciona com usuário logado, inclusive bloqueado.
[CmdletBinding()]
param(
    [ValidateRange(5, 1440)][int]$EmailIntervalMinutes = 15,
    [string]$JobsAt = '09:00',
    [switch]$StartDisabled
)
$ErrorActionPreference = 'Stop'
$taskDir = $PSScriptRoot
$taskPython = Join-Path $taskDir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Crie a .venv e instale requirements.txt antes de registrar as tarefas.'
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$emailTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EmailIntervalMinutes)
$jobTrigger = New-ScheduledTaskTrigger -Daily -At $JobsAt
$definitions = @(
    @{ Name = 'IA - Emails urgentes'; Script = 'email_classifier.py'; Trigger = $emailTrigger },
    @{ Name = 'IA - Vagas Junior'; Script = 'job_finder.py'; Trigger = $jobTrigger }
)
foreach ($definition in $definitions) {
    $scriptPath = Join-Path $taskDir $definition.Script
    $action = New-ScheduledTaskAction -Execute $taskPython `
        -Argument ('"' + $scriptPath + '"') -WorkingDirectory $taskDir
    if (Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue) {
        Write-Host ('Tarefa já existente, preservada: ' + $definition.Name)
        continue
    }
    $taskDefinition = New-ScheduledTask -Action $action -Trigger $definition.Trigger `
        -Settings $settings -Principal $principal `
        -Description 'Automação Python sem interface; logs em LocalAppData\IAAutomacoes.'
    if ($StartDisabled) {
        # Registra já desativada, sem janela de execução com credenciais ausentes.
        $taskDefinition.Settings.Enabled = $false
    }
    Register-ScheduledTask -TaskName $definition.Name -InputObject $taskDefinition
}
Write-Host 'Tarefas registradas. Consulte o Agendador de Tarefas e os logs locais.'
