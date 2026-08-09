'This sends txt via the sendkeys statement to what txtarea has focus at the time
'Repeatedly until the variable 'mynum' is reached

set a = createobject("wscript.shell")

mynum=inputbox("How many times to leave and join the teamtalk channle?","spamnumber","5") 
myspeed=inputbox("How fast to spam..In milisecs!","delay","200") 
mywait=inputbox("Time to wait proir to sending the spam msg in secs","Wait?","50")

msgbox("You have " & mywait & " secs to put focus on your target text area!")
wscript.sleep (mywait*1000) 
for i=1 to mynum 		'count down from mynum variable
	a.sendkeys "^l"       'Sends the text you typed in the mytxt prompt
	a.sendkeys ("{ENTER}")   'presses the enter key to send your text you may change it to the apropriate key that sends the msg in your chat
	wscript.sleep (myspeed)   'sleeps OR waits the amount of Milseconds you typed in the Mywait prompt
next
msgbox("Finished Spamming!")
