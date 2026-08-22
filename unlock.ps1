# To make PowerShell fully accept and output Unicode (UTF-8),
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

#
# RemoteSigned (rather than Bypass) is the safer permanent choice — it
# allows locally-created scripts (like these) to run freely, but still
# requires a digital signature for scripts downloaded from the internet,
# so you're not fully disabling the protection, just exempting your own
# local files.
# Set for your user account permanently (doesn't need admin)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

#
# Windows tags downloaded files with a hidden "Mark of the Web" (an NTFS
# alternate data stream called Zone.Identifier), so PowerShell treats it
# as remote.
# Fix — strip that tag from the file, to make them local files:
Unblock-File .\install.ps1
Unblock-File .\run.ps1
Unblock-File .\check-connections.ps1
Unblock-File .\test-backend.ps1
Unblock-File .\test-frontend.ps1
Unblock-File .\backend\Activate.ps1
Unblock-File .\backend\Deactivate.ps1