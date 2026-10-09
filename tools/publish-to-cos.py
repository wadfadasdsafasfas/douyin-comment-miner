#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前版本的 6 个安装包发布到腾讯云 COS 并逐个校验。

用法：
  密钥放 /tmp/.cos_keys（每行 "SecretId xxx" / "SecretKey xxx"，chmod 600）
  python3 tools/publish-to-cos.py

说明：
- 桶：tingchao-downloads-<APPID>（ap-beijing，标准存储，公开读），已存在则跳过建桶
- Windows 包发布名固定为 TingChao-windows-v<版本>-setup.exe / -portable.zip，
  与官网按钮、后台 /api/latest 的 downloads 字段保持一致
- 上传后按 ETag(=整包 md5) 校验，别再把旧制品当新版发出去
"""

import sys, os, hashlib, json
from qcloud_cos import CosConfig, CosS3Client

keys = {}
for line in open('/tmp/.cos_keys'):
    k, v = line.split()
    keys[k] = v
SECRET_ID, SECRET_KEY = keys['SecretId'], keys['SecretKey']
APPID = '1315442697'   # CAM GetUserAppId 实测值（主账号 ID 100028693851 是 UIN，不是 APPID）
REGION = 'ap-beijing'
BUCKET = f'tingchao-downloads-{APPID}'

VERSION = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'electron', 'package.json')))['version']
REL = os.path.join(os.path.dirname(__file__), '..', 'electron', 'release')
WIN = os.environ.get('WIN_DIR', f'/tmp/winout-latest')   # 解包 CI windows-packages 后的目录
FILES = [
    (f'{REL}/TingChao-macos-v{VERSION}-arm64.dmg', f'TingChao-macos-v{VERSION}-arm64.dmg'),
    (f'{REL}/TingChao-macos-v{VERSION}-arm64.zip', f'TingChao-macos-v{VERSION}-arm64.zip'),
    (f'{REL}/TingChao-macos-v{VERSION}-x64.dmg',   f'TingChao-macos-v{VERSION}-x64.dmg'),
    (f'{REL}/TingChao-macos-v{VERSION}-x64.zip',   f'TingChao-macos-v{VERSION}-x64.zip'),
    (f'{WIN}/TingChao-windows-v{VERSION}-setup.exe',    f'TingChao-windows-v{VERSION}-setup.exe'),
    (f'{WIN}/TingChao-windows-v{VERSION}-x64.zip',      f'TingChao-windows-v{VERSION}-portable.zip'),
]

client = CosS3Client(CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY, Scheme='https'))

def log(*a): print(*a, flush=True)

# 1) 建桶（标准存储，最便宜实用的公开读方案；已存在则跳过）
try:
    client.create_bucket(Bucket=BUCKET, ACL='public-read')
    log(f'✓ 建桶 {BUCKET} (ap-beijing, 标准存储, 公开读)')
except Exception as e:
    if 'BucketAlreadyOwnedByYou' in str(e) or 'already' in str(e).lower():
        log(f'桶已存在，继续')
    else:
        log('建桶失败:', e); sys.exit(1)

# 2) 上传 + 校验
report = []
for local, key in FILES:
    size = os.path.getsize(local)
    md5 = hashlib.md5(open(local,'rb').read()).hexdigest()
    client.put_object_from_local_file(Bucket=BUCKET, Key=key, LocalFilePath=local)
    head = client.head_object(Bucket=BUCKET, Key=key)
    etag = head['ETag'].strip('"').lower()
    ok = (int(head['Content-Length']) == size) and (etag == md5)
    report.append((key, size, md5[:8], etag[:8], 'OK' if ok else 'MISMATCH'))
    log(('✓' if ok else '✗'), key, size, 'etag', etag[:8], 'local', md5[:8])

# 3) 桶 ACL 兜底再确认一次
acl = client.get_bucket_acl(Bucket=BUCKET)
grants = [g for g in acl['Grants'] if 'AllUsers' in str(g.get('Grantee',{}).get('URI',''))]
log('公开读授权:', '有' if grants else '无!')

json.dump([{ 'key': k, 'size': s, 'md5': m} for k,s,m,_,_ in report], open('/tmp/cos_report.json','w'))
log('DONE')
