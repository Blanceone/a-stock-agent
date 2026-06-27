@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title A-Stock Agent
cd /d "%~dp0"

:MENU
cls
echo.
echo  +=====================================================+
echo  ^|       A股宏观锚定投研智能体 - 控制面板              ^|
echo  +-----------------------------------------------------+
echo  ^|                                                     ^|
echo  ^|  [1] 一键启动 (SSH + 动态监控 + API)                ^|
echo  ^|  [2] 首次初始化 (建表 + 全A股入库)                  ^|
echo  ^|  [3] 语义知识库初始化 (ChromaDB向量化)               ^|
echo  ^|  [4] 仅启动动态监控                                  ^|
echo  ^|  [5] 构建静态图谱 (需政策PDF)                        ^|
echo  ^|  [6] SOP审核平台 (API Server)                        ^|
echo  ^|  [7] 运行测试套件                                    ^|
echo  ^|  [8] 查看系统输出                                    ^|
echo  ^|  [9] 安全关闭所有服务                                 ^|
echo  ^|                                                     ^|
echo  ^|  [Q] 退出                                           ^|
echo  ^|                                                     ^|
echo  +=====================================================+
echo.
set /p choice="  请选择 [1-9/Q]: "

if /i "%choice%"=="1" goto START_ALL
if /i "%choice%"=="2" goto INIT
if /i "%choice%"=="3" goto SEMANTIC
if /i "%choice%"=="4" goto DYNAMIC
if /i "%choice%"=="5" goto STATIC
if /i "%choice%"=="6" goto API
if /i "%choice%"=="7" goto TEST
if /i "%choice%"=="8" goto VIEW
if /i "%choice%"=="9" goto SHUTDOWN
if /i "%choice%"=="Q" exit /b
if /i "%choice%"=="q" exit /b
echo  [!] 无效选择
timeout /t 2 >nul
goto MENU

:START_ALL
cls
echo.
echo  =====================================================
echo    一键启动 - 完整模式
echo  =====================================================
echo.
echo  [0/3] 清理旧进程...
taskkill /FI "WINDOWTITLE eq SSH-Tunnel*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq API-Server*" /F >nul 2>&1
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list 2^>nul ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "main.py --mode dynamic" >nul && (
        taskkill /PID %%a /F >nul 2>&1
        echo    已终止旧的动态监控进程
    )
)
echo    旧进程已清理
echo.
echo  [1/3] 启动 SSH 隧道 (PG:5432 / Redis:6379 / ChromaDB:8000)...
start "SSH-Tunnel" cmd /k "python scripts\start_ssh_tunnels.py"
timeout /t 5 >nul

echo  [2/3] 启动 SOP 审核平台 (http://localhost:8088)...
start "API-Server" cmd /k "uvicorn api:app --host 0.0.0.0 --port 8088"
timeout /t 3 >nul

echo  [3/3] 启动动态监控流水线...
echo.
echo  +-----------------------------------------------------+
echo  ^|  SSH隧道  : 新窗口 (SSH-Tunnel)                     ^|
echo  ^|  API服务  : 新窗口 (API-Server)                      ^|
echo  ^|             -- http://localhost:8088                  ^|
echo  ^|  动态监控 : 当前窗口 (Ctrl+C 停止)                   ^|
echo  +-----------------------------------------------------+
echo.
python main.py --mode dynamic
goto MENU

:INIT
cls
echo.
echo  =====================================================
echo    首次初始化 (建表 + 全A股入库)
echo  =====================================================
echo.
echo  [1/2] 启动 SSH 隧道...
start "SSH-Tunnel" cmd /k "python scripts\start_ssh_tunnels.py"
timeout /t 5 >nul
echo  [2/2] 执行初始化...
python main.py --mode init
echo.
pause
goto MENU

:SEMANTIC
cls
echo.
echo  =====================================================
echo    语义知识库初始化
echo  =====================================================
echo.
echo  向量化主营业务文本 -- ChromaDB ...
echo  (约 5307 只股票, 预计耗时 10-30 分钟)
echo.
python main.py --mode semantic
echo.
pause
goto MENU

:DYNAMIC
cls
echo.
echo  =====================================================
echo    动态监控模式
echo  =====================================================
echo.
echo  每1分钟轮询财联社电报 -- 新闻漏斗 -- 三共振检查
echo  按 Ctrl+C 停止
echo.
python main.py --mode dynamic
goto MENU

:STATIC
cls
echo.
echo  =====================================================
echo    静态图谱构建
echo  =====================================================
echo.
set /p pdf="  请输入政策 PDF 完整路径: "
if not defined pdf (
    echo  [!] 未输入路径
    pause
    goto MENU
)
if not exist "%pdf%" (
    echo  [!] 文件不存在: %pdf%
    pause
    goto MENU
)
python main.py --mode static --pdf "%pdf%"
echo.
pause
goto MENU

:API
cls
echo.
echo  =====================================================
echo    SOP 审核平台
echo  =====================================================
echo.
echo  启动中... 浏览器访问 http://localhost:8088
uvicorn api:app --host 0.0.0.0 --port 8088
goto MENU

:TEST
cls
echo.
echo  =====================================================
echo    测试套件
echo  =====================================================
echo.
echo  [a] 全部测试 (90 个, 约 3 分钟)
echo  [b] 仅单元测试 (56 个, 约 2 秒)
echo  [c] E2E 集成测试 (25 个, 约 35 秒)
echo  [d] 全业务链路测试 (9 个, 约 2 分钟)
echo.
set /p tc="  请选择: "
if /i "%tc%"=="a" pytest tests/ -v --tb=short
if /i "%tc%"=="b" pytest tests\test_phase1.py tests\test_phase2.py tests\test_phase3.py tests\test_phase4.py -v
if /i "%tc%"=="c" pytest tests\test_e2e.py -v -s
if /i "%tc%"=="d" pytest tests\test_full_chain.py -v -s --tb=short
echo.
pause
goto MENU

:SHUTDOWN
cls
echo.
echo  =====================================================
echo    安全关闭所有服务
echo  =====================================================
echo.
echo  [1/2] 通过 API 停止后台任务...
curl -s -X POST http://localhost:8088/api/shutdown 2>nul
echo.
echo  [2/2] 清理残留进程...
taskkill /FI "WINDOWTITLE eq SSH-Tunnel*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq API-Server*" /F >nul 2>&1
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list 2^>nul ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "main.py" >nul && (
        taskkill /PID %%a /F >nul 2>&1
        echo    已终止 python 进程 PID=%%a
    )
)
echo.
echo  所有服务已安全关闭。
pause
goto MENU

:VIEW
cls
echo.
python scripts\view_output.py
pause
goto MENU
