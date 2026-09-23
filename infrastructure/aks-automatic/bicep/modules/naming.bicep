@export()
func resourceNames(prefix string, environmentName string, subscriptionId string) object => {
  group: 'rg-${prefix}-${environmentName}-${take(uniqueString(subscriptionId, environmentName, prefix), 8)}'
  base: '${prefix}-${environmentName}-${take(uniqueString(subscriptionId, environmentName, prefix), 8)}'
  registry: 'acr${replace(prefix, '-', '')}${uniqueString(subscriptionId, environmentName, prefix)}'
  vault: 'kv${take(replace(prefix, '-', ''), 8)}${uniqueString(subscriptionId, environmentName, prefix)}'
}
