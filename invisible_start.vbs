Set WshShell = CreateObject("WScript.Shell")

' Runs the Python server completely hidden
WshShell.Run "python ""C:\Users\Agilent\Documents\Hamilton-MicroLab-Prep-Interface\server.py""", 0, False

' Runs the browser launcher hidden (the browser itself will still open visibly)
WshShell.Run """C:\Users\Agilent\Documents\Hamilton-MicroLab-Prep-Interface\launch.bat""", 0, False
