@echo off
chcp 65001 >nul
echo ============================================================
echo   配置 A股尾盘选股定时任务
echo   每个交易日 14:30 自动触发
echo ============================================================
echo.

set PYTHON_PATH=python
set SCRIPT_DIR=%~dp0
set SCHEDULER=%SCRIPT_DIR%scheduler.py

echo Python: %PYTHON_PATH%
echo 脚本:   %SCHEDULER_PATH%
echo.

REM 删除旧任务（如果存在）
schtasks /delete /tn "A股尾盘选股" /f >nul 2>&1

REM 创建新任务
schtasks /create ^
  /tn "A股尾盘选股" ^
  /tr "\"%PYTHON_PATH%\" \"%SCHEDULER%\"" ^
  /sc weekly ^
  /d MON,TUE,WED,THU,FRI ^
  /st 14:30 ^
  /ru "%USERNAME%" ^
  /f

if %errorlevel%==0 (
  echo.
  echo ✅ 定时任务创建成功！
  echo    每个交易日 14:30 自动启动
  echo    14:40 准时执行选股扫描
  echo.
  echo 查看/管理: Win+R → taskschd.msc → 搜索 "A股尾盘选股"
) else (
  echo.
  echo ❌ 创建失败。请尝试以管理员身份运行。
  echo    或手动配置: Win+R → taskschd.msc → 创建任务
)

pause
