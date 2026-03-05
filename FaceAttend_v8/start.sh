#!/bin/bash
echo "FaceAttend Pro V7 - Starting..."
pip3 install flask -q 2>/dev/null
sleep 0.5
open http://localhost:5000 2>/dev/null || xdg-open http://localhost:5000 2>/dev/null &
python3 app.py
