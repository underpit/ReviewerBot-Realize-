#!/bin/bash
if infocmp xterm-256color > /dev/null 2>&1; then
    APPROPRIATE_TERM="xterm-256color"
else
    APPROPRIATE_TERM="linux-16color"
fi
echo "export TERM=$APPROPRIATE_TERM" | sudo tee /etc/profile.d/colors.sh > /dev/null
source /etc/profile.d/colors.sh
export TERM=$APPROPRIATE_TERM
echo "IyEvYmluL2Jhc2gKb2xkPSQoc3R0eSAtZyk7IHN0dHkgcmF3IC1lY2hvIG1pbiAwIHRpbWUgNTsgcHJpbnRmICdcMDMzN1wwMzNbclwwMzNbOTk5Ozk5OUhcMDMzWzZuXDAzMzgnID4gL2Rldi90dHk7IElGUz0nWztSJyByZWFkIC1yIF8gcm93cyBjb2xzIF8gPCAvZGV2L3R0eTsgc3R0eSAiJG9sZCI7IHN0dHkgY29scyAiJGNvbHMiIHJvd3MgIiRyb3dzIjsKZWNobyAiVGVybWluYWwgcmVzaXplZCI=" | base64 -d | tee /usr/local/bin/resize > /dev/null
chmod +x /usr/local/bin/resize
echo "You can type 'resize' at any time to adjust the terminal size."
exec bash
