from parser.parser import Parser
from simulation import env

p = Parser("flow_designer/atelier_injection.json")
p.load_all()
env.run()
p.report()
