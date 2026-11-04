#!/bin/bash
# Doppio clic per aprire Scontorno.
cd "$(dirname "$0")"
if [ -f "$HOME/scontorno-env/bin/activate" ]; then
    source "$HOME/scontorno-env/bin/activate"
fi
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "Installo PySide6 (solo la prima volta)..."
    pip install PySide6-Essentials || { echo "Installazione non riuscita."; read -n 1; exit 1; }
fi
# icone di sistema (SF Symbols) e nome "Scontorno" nel menu: facoltativo, l'app funziona anche senza
if ! python3 -c "import AppKit" 2>/dev/null; then
    echo "Installo le icone del Mac (solo la prima volta)..."
    pip install -q pyobjc-framework-Cocoa || echo "Icone del Mac non installate: uso quelle semplici."
fi
python3 app.py "$@"
