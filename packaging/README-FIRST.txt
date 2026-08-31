JobAgent Windows portable release candidate
===========================================

1. Extract the entire ZIP to a normal local folder.
2. In Chrome, open chrome://extensions, enable Developer mode, choose
   "Load unpacked", and select the included "extension" folder.
3. Keep a normal, logged-in BOSS tab available when you intentionally start a
   bounded search.
4. Double-click JobAgent.exe. The local console opens at 127.0.0.1:8000.
5. To stop it, double-click Stop-JobAgent.cmd.

User data is stored under %LOCALAPPDATA%\JobAgent and is not part of this ZIP.
No automatic bulk application, background application, recruiter follow-up,
CAPTCHA bypass, stealth, cookie/token extraction, or credential export exists.

This candidate is not code-signed yet. Windows may show an unknown-publisher
warning. Verify SHA256SUMS.txt against the value published with the release.
