' 连真机-静默.vbs —— 双击后不弹黑窗口，后台把桥接和 adb reverse 架好。
' 关掉用同目录的 断开桥接.cmd。
Option Explicit

Dim fso, sh, here, cmd, rc
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = """" & here & "\连真机.cmd"" quiet"

' 0 = 隐藏窗口, True = 等它跑完（脚本内部已经把等待做进去了）
rc = sh.Run("cmd /c " & cmd, 0, True)

If rc <> 0 Then
    ' 失败时把 bridge.log 的尾巴念出来，总比"点了没反应"强
    Dim logPath, logText
    logPath = fso.GetParentFolderName(here) & "\.gtmp\bridge.log"
    logText = ""
    If fso.FileExists(logPath) Then
        Dim ts
        Set ts = fso.OpenTextFile(logPath, 1)
        If Not ts.AtEndOfStream Then logText = ts.ReadAll()
        ts.Close
        If Len(logText) > 600 Then logText = Right(logText, 600)
    End If
    MsgBox "桥接没起来。" & vbCrLf & vbCrLf & _
           "常见原因：电台没插 / 串口被别的程序占着 / python 没装。" & vbCrLf & _
           "想看详细过程就双击 连真机.cmd（有窗口那版）。" & vbCrLf & vbCrLf & logText, _
           16, "连真机"
End If
