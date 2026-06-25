# Infra — one or many
python master.py --infra postgres,mongo,redis --recreate
python master.py --infra all --recreate --new

# Import — one or all (continues if one fails)
python master.py --import mongo --backup-zip .\backup.zip --existing
python master.py --import all --backup-zip .\backup.zip --merge

# Deploy / stop — one or all
python master.py --deploy agentic_backend
python master.py --deploy all
python master.py --stop mcp_server
python master.py --stop all

# Full stack
python master.py --infra all --import all --deploy all --backup-zip .\backup.zip --new

# Status / teardown
python master.py --status
python master.py --down