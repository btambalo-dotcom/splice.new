@echo off
setlocal
title Splice - Atualizar banco e iniciar sistema
cd /d %~dp0

echo ===========================================
echo  CRIANDO/ATIVANDO VENV + INSTALANDO DEPENDENCIAS
echo ===========================================
echo.

REM Cria venv se ainda nao existir
if not exist venv\Scripts\python.exe (
  python -m venv venv
)

call venv\Scripts\activate.bat

REM Garante pip atualizado e instala requirements
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ===========================================
echo  ATUALIZANDO BANCO (colunas novas)
echo ===========================================
echo.

python fix_db.py

echo.
echo ===========================================
echo  INICIANDO SISTEMA
echo ===========================================
echo.

python app.py

echo.
echo (Janela pode ser fechada quando desejar.)
pause
endlocal
