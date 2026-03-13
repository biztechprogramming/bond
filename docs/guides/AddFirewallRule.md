# Add all three with correct WSL IP
netsh interface portproxy add v4tov4 listenport=18788 listenaddress=0.0.0.0 connectport=18788 connectaddress=172.24.107.162
netsh interface portproxy add v4tov4 listenport=18789 listenaddress=0.0.0.0 connectport=18789 connectaddress=172.24.107.162
netsh interface portproxy add v4tov4 listenport=18790 listenaddress=0.0.0.0 connectport=18790 connectaddress=172.24.107.162

# Add firewall rules if missing
netsh advfirewall firewall add rule name="Bond Gateway 18789" dir=in action=allow protocol=tcp localport=18789
netsh advfirewall firewall add rule name="Bond Backend 18790" dir=in action=allow protocol=tcp localport=18790

# Verify
netsh interface portproxy show all
