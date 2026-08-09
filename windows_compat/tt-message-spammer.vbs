'This sends txt through the clipboard to what txtarea has focus at the time
'Repeatedly until the variable 'mynum' is reached

set a = createobject("wscript.shell")

Sub SetClipboard(text)
	Set html = CreateObject("htmlfile")
	html.parentWindow.clipboardData.setData "text", text
End Sub

mytxt=inputbox("Send what text?","Type some text","Oh Yeah!")
mynum=inputbox("How many times to send the text?","spamnumber","3") 
myspeed=inputbox("How fast to spam..In milisecs!","delay","200") 
mywait=inputbox("Time to wait proir to sending the spam msg in secs","Wait?","20")

msgbox("You have " & mywait & " secs to put focus on the message box!")
wscript.sleep (mywait*1000) 
SetClipboard mytxt
for i=1 to mynum 		'count down from mynum variable
	a.sendkeys "^v"       'Pastes the text you typed in the mytxt prompt
	a.sendkeys ("{ENTER}")   'presses the enter key to send your text you may change it to the apropriate key that sends the msg in your chat
	wscript.sleep (myspeed)   'sleeps OR waits the amount of Milseconds you typed in the Mywait prompt
next
msgbox("Finished Spamming!")
