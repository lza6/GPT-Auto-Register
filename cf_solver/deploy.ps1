param(
  [string]$HostName = '38.76.213.112',
  [string]$User = 'root',
  [string]$KeyPath = '..\..\服务器凭证\ssh秘钥.txt'
)

$ErrorActionPreference = 'Stop'
$remote = "${User}@${HostName}"
$target = '/opt/boterdrop-solver'

scp -i $KeyPath -r api_server.py boterdrop_wrapper.py config.json requirements.txt ecosystem.config.cjs $remote`:$target/
if (-not $?) { throw 'scp 上传失败' }

ssh -i $KeyPath $remote "cd $target && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt && pm2 startOrReload ecosystem.config.cjs && pm2 save"
if (-not $?) { throw '远程部署失败' }

ssh -i $KeyPath $remote "pm2 list && ss -tlnp | grep 8001 && curl -s -m 90 'http://127.0.0.1:8001/turnstile?url=https://app.pixverse.ai/register&sitekey=0x4AAAAAAATSS5Nb9KyiA05l'"
if (-not $?) { throw '部署验证失败' }
