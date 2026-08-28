@echo off
echo Parando o motor Freebuff (apenas os processos do projeto)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and ($_.CommandLine -like '*motor_rt_alphaz*' -or $_.CommandLine -like '*watchdog*' -or $_.CommandLine -like '*freebuff*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Concluido.
