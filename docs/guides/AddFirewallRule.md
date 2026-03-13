New-NetFirewallRule -DisplayName "WSL Web Server Port 18788" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18788

netsh interface portproxy add v4tov4 listenport=18788 listenaddress=0.0.0.0 connectport=18788 connectaddress=$(wsl hostname -I).Trim()
