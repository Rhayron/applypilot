@echo off
REM ============================================================
REM  AutoCV - rebuild do container com o codigo novo
REM  Basta dar duplo-clique neste arquivo.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === Reconstruindo e subindo o container autocv ===
echo Pasta: %cd%
echo.

REM Docker Compose v2 (recomendado)
docker compose up -d --build
if %errorlevel% neq 0 (
    echo.
    echo "docker compose" falhou, tentando o antigo "docker-compose"...
    docker-compose up -d --build
)

echo.
echo === Containers em execucao ===
docker ps --filter "name=autocv" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"

echo.
echo === Ultimas linhas de log ===
docker compose logs --tail 15 2>nul || docker-compose logs --tail 15

echo.
echo === Verificando a tabela cycle_runs no banco ===
docker compose exec -T autoapply python -c "import sqlite3;print('Tabelas:',[r[0] for r in sqlite3.connect('/data/autoapply.db').execute(\"select name from sqlite_master where type='table'\")])" 2>nul || docker-compose exec -T autoapply python -c "import sqlite3;print('Tabelas:',[r[0] for r in sqlite3.connect('/data/autoapply.db').execute(\"select name from sqlite_master where type='table'\")])"

echo.
echo === Pronto. Pressione qualquer tecla para fechar. ===
pause >nul
endlocal
