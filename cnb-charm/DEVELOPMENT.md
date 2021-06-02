# Development nodes

## Deploy charm

```
juju deploy ./cnb-charm/cnb.charm --resource application-image=<image_name>
```

## Update charm

From the project root:

```
(cd cnb-charm; charmcraft pack)
juju refresh cnb --model test-app --path ./cnb-charm/cnb.charm
juju debug-log
```