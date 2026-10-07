import pulumi
from resources import build

for name, value in build(pulumi.Config().require_object('infrastructure')).items():
    pulumi.export(name, value)
