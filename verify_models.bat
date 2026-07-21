@echo off
REM Testa os modelos Gemini configurados fazendo uma chamada real (auth + id).
setlocal
cd /d "%~dp0"
echo === Testando chamada real aos modelos Gemini no container ===
echo (a saida vai para _modelcheck_out.txt)
docker compose exec -T autoapply python /data/_modelcheck.py > "%~dp0_modelcheck_out.txt" 2>&1 || docker-compose exec -T autoapply python /data/_modelcheck.py > "%~dp0_modelcheck_out.txt" 2>&1
type "%~dp0_modelcheck_out.txt"
echo.
echo === Fim. Pressione qualquer tecla para fechar. ===
pause >nul
endlocal
