# Give to Grok Build / run locally

The chat preview at 127.0.0.1:8765 runs in the model sandbox. Your phone cannot open it.

## Landed location

GitHub: Oneeleven1988068/jingcai-stage3
Path: skills/jingcai-stage3/pm_jc_align/

## Run on your computer

```bash
git clone https://github.com/Oneeleven1988068/jingcai-stage3.git
cd jingcai-stage3
git checkout feat/pm-jc-align
cd skills/jingcai-stage3/pm_jc_align
python3 server.py
```

Open http://127.0.0.1:8765

## Prompt to paste into a new Grok Build chat

```
Read GitHub Oneeleven1988068/jingcai-stage3 branch feat/pm-jc-align path skills/jingcai-stage3/pm_jc_align/.
Read-only Jingcai 3.4 x Polymarket consensus terminal.
No orders, no wallet, no private keys.
Run server.py or wire web/index.html to /api/snapshot.
Sources: Polymarket Gamma/CLOB public APIs; Sporttery via data/jc_cache.json or POST /api/ingest-jc.
UI palette ink #0B0C0A paper #ECEAE4 sage #7D9B8A brick #B07060. No neon.
```
