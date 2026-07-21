@echo off
REM ============================================================
REM  AutoCV - restart do container (aplica mudancas do config.yaml)
REM  Nao faz rebuild (config e lido no boot). Duplo-clique para rodar.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === Reiniciando o container autocv ===
docker compose restart 2>nul || docker-compose restart

echo.
echo Aguardando o primeiro ciclo apos o restart (45s)...
timeout /t 45 /nobreak >nul

echo.
echo === Ultimas 60 linhas de log ===
docker compose logs --tail 60 2>nul || docker-compose logs --tail 60

echo.
echo === Procurando erros de autenticacao / modelo ===
docker compose logs --tail 200 2>nul > "%temp%\autocv_log.txt" || docker-compose logs --tail 200 > "%temp%\autocv_log.txt"
findstr /I "error auth apikey api_key model_not_found notfound 401 403 404 exception traceback litellm" "%temp%\autocv_log.txt"
if %errorlevel%==0 (
    echo.
    echo [ATENCAO] Foram encontradas linhas suspeitas acima.
) else (
    echo Nenhum erro de auth/modelo encontrado nos logs recentes.
)

echo.
echo === Pronto. Pressione qualquer tecla para fechar. ===
pause >nul
endlocal
