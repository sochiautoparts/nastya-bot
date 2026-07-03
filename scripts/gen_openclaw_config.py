#!/usr/bin/env python3
"""
Generate OpenClaw config dynamically based on which API keys are present.
Pollinations is always included (free, no key needed).
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

PROVIDERS = [
    {"id":"pollinations","baseUrl":"https://text.pollinations.ai/openai","api":"openai-completions","timeoutSeconds":12,"always":True,"models":[{"id":"openai","name":"Pollinations GPT-OSS 20B (free)"}]},
    {"id":"groq","baseUrl":"https://api.groq.com/openai/v1","api":"openai-completions","env":"GROQ_API_KEY","timeoutSeconds":12,"models":[{"id":"llama-3.3-70b-versatile","name":"Groq Llama 3.3 70B"},{"id":"llama-3.1-8b-instant","name":"Groq Llama 3.1 8B (fast)"},{"id":"deepseek-r1-distill-llama-70b","name":"Groq DeepSeek R1 70B"},{"id":"qwen-2.5-32b","name":"Groq Qwen 2.5 32B"}]},
    {"id":"gemini","baseUrl":"https://generativelanguage.googleapis.com/v1beta/openai","api":"openai-completions","env":"GEMINI_API_KEY","timeoutSeconds":12,"models":[{"id":"gemini-2.0-flash","name":"Gemini 2.0 Flash"},{"id":"gemini-1.5-flash","name":"Gemini 1.5 Flash"}]},
    {"id":"openrouter","baseUrl":"https://openrouter.ai/api/v1","api":"openai-completions","env":"OPENROUTER_API_KEY","timeoutSeconds":12,"models":[{"id":"meta-llama/llama-3.3-70b-instruct:free","name":"OpenRouter Llama 3.3 70B (free)"},{"id":"google/gemma-4-31b-it:free","name":"OpenRouter Gemma 4 31B (free)"},{"id":"qwen/qwen3-next-80b-a3b-instruct:free","name":"OpenRouter Qwen3 Next 80B (free)"}]},
    {"id":"huggingface","baseUrl":"https://router.huggingface.co/v1","api":"openai-completions","env":"HF_TOKEN","timeoutSeconds":12,"models":[{"id":"qwen2.5-7b-instruct","name":"HF Qwen2.5 7B"},{"id":"meta-llama/Llama-3.1-8B-Instruct","name":"HF Llama 3.1 8B"}]},
    {"id":"cerebras","baseUrl":"https://api.cerebras.ai/v1","api":"openai-completions","env":"CEREBRAS_API_KEY","timeoutSeconds":12,"models":[{"id":"llama-3.3-70b","name":"Cerebras Llama 3.3 70B"},{"id":"llama3.1-8b","name":"Cerebras Llama 3.1 8B"}]},
    {"id":"cloudflare","baseUrl":"https://api.cloudflare.com/client/v4/accounts/CF_ACCOUNT_ID/ai/v1","api":"openai-completions","env":"CF_API_TOKEN_1","env_account":"CF_ACCOUNT_ID_1","timeoutSeconds":12,"models":[{"id":"@cf/meta/llama-3.1-8b-instruct","name":"CF Llama 3.1 8B"},{"id":"@cf/qwen/qwen1.5-14b-chat-awq","name":"CF Qwen 1.5 14B"}]},
    {"id":"openai","baseUrl":"https://api.openai.com/v1","api":"openai-completions","env":"OPENAI_API_KEY","timeoutSeconds":12,"models":[{"id":"gpt-4o-mini","name":"OpenAI GPT-4o mini"}]},
    {"id":"anthropic","baseUrl":"https://api.anthropic.com/v1","api":"anthropic-messages","env":"ANTHROPIC_API_KEY","timeoutSeconds":12,"models":[{"id":"claude-3-5-sonnet-latest","name":"Claude 3.5 Sonnet"}]},
    {"id":"mistral","baseUrl":"https://api.mistral.ai/v1","api":"openai-completions","env":"MISTRAL_API_KEY","timeoutSeconds":12,"models":[{"id":"mistral-small-latest","name":"Mistral Small"}]},
    {"id":"sambanova","baseUrl":"https://api.sambanova.ai/v1","api":"openai-completions","env":"SAMBANOVA_API_KEY","timeoutSeconds":12,"models":[{"id":"Meta-Llama-3.1-8B-Instruct","name":"SambaNova Llama 3.1 8B"}]},
]
PRIORITY = ["groq","gemini","cerebras","openrouter","huggingface","sambanova","mistral","cloudflare","openai","anthropic","pollinations"]

def _env_set(n): v=os.getenv(n,"").strip(); return bool(v) and v.lower() not in ("not_configured","none","null")
def _idx(pid):
    for i,p in enumerate(PROVIDERS):
        if p["id"]==pid: return i
    return 0

def build_config():
    provs={}; active=[]
    for p in PROVIDERS:
        if p.get("always") or (p.get("env") and _env_set(p["env"])):
            base_url = p["baseUrl"]
            # Cloudflare: substitute account ID from env
            if "CF_ACCOUNT_ID" in base_url and p.get("env_account"):
                acct = os.getenv(p["env_account"], "").strip()
                if acct:
                    base_url = base_url.replace("CF_ACCOUNT_ID", acct)
                else:
                    continue  # skip cloudflare if no account ID
            e={"baseUrl":base_url,"api":p["api"],"timeoutSeconds":p["timeoutSeconds"],"models":p["models"]}
            if p.get("always"):
                if p.get("apiKey"): e["apiKey"]=p["apiKey"]
            else:
                e["apiKey"]={"source":"env","provider":"default","id":p["env"]}
            provs[p["id"]]=e; active.append(p["id"])
    ordered=[pid for pid in PRIORITY if pid in active] or ["pollinations"]
    primary=f"{ordered[0]}/{PROVIDERS[_idx(ordered[0])]['models'][0]['id']}"
    fallbacks=[f"{pid}/{PROVIDERS[_idx(pid)]['models'][0]['id']}" for pid in ordered[1:]]
    return {"$schema":"https://docs.openclaw.ai/schema/openclaw.json","gateway":{"port":int(os.getenv("OPENCLAW_PORT","18789")),"bind":"loopback","auth":{"mode":"none"},"http":{"endpoints":{"chatCompletions":{"enabled":True,"maxBodyBytes":8388608}}},"controlUi":{"enabled":False}},"agents":{"defaults":{"model":{"primary":primary,"fallbacks":fallbacks},"params":{"temperature":0.9},"skipBootstrap":True,"workspace":"."}},"models":{"mode":"merge","providers":provs}}

def main():
    out_arg=None; state_dir=os.getenv("OPENCLAW_STATE_DIR") or os.path.expanduser("~/.openclaw"); args=sys.argv[1:]; i=0
    while i<len(args):
        if args[i] in ("--out","-o") and i+1<len(args): out_arg=args[i+1]; i+=2
        elif args[i]=="--state-dir" and i+1<len(args): state_dir=args[i+1]; i+=2
        else: i+=1
    cfg=build_config(); out_path=Path(out_arg) if out_arg else Path(state_dir)/"openclaw.json"
    out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(cfg,indent=2,ensure_ascii=False))
    active=[pid for pid in PRIORITY if pid in cfg["models"]["providers"]]
    print(f"[gen_openclaw_config] wrote {out_path}"); print(f"[gen_openclaw_config] active: {active}"); print(f"[gen_openclaw_config] primary: {cfg['agents']['defaults']['model']['primary']}")
    return 0

if __name__=="__main__": sys.exit(main())
