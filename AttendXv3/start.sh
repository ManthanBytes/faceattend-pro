#!/bin/bash
pip3 install flask -q 2>/dev/null || pip install flask -q 2>/dev/null
(sleep 1 && open http://localhost:5000 2>/dev/null || xdg-open http://localhost:5000 2>/dev/null) &
python3 app.py
