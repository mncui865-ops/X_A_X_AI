
import os, io, re, json, sys, time, base64, hashlib, traceback
import urllib.request, urllib.parse

# ==================== الإعدادات ====================
BOT_TOKEN ="8635854938:AAGORHtgaeVdx1e6J4nfECIFayi85k5wfvE"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OUTPUT_FILE = "output.json"
# ==================================================

try:
    from Crypto.Cipher import AES
    from Crypto.Hash import SHA1
    HAS_AES = True
except ImportError:
    print("[!] pip install pycryptodome")
    sys.exit(1)

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False
    print("[!] msgpack غير مثبت. DarkTunnel لن يعمل. pip install msgpack")

# ============================================================
# 1. SlipNet
# ============================================================
SLIPNET_KEY_HEX = "214F052025B2F949605A5429EC3D5FA80C2022C168AD946E68852D447214DBD3"
SLIPNET_KEY = bytes.fromhex(SLIPNET_KEY_HEX)
SLIPNET_PBKDF2_ITER = 600000
SLIPNET_SALT_LEN = 16
SLIPNET_IV_LEN = 12

SLIPNET_SCHEMAS = {
    "1": ["Version","Tunnel Type/Mode","Name","Domain","Resolvers","AuthMode","KeepAlive","CC","Port","Host","GSO"],
}
_v1 = SLIPNET_SCHEMAS["1"]
_v20 = _v1 + ["DNSTT Public Key","SOCKS Username","SOCKS Password","SSH Enabled","SSH Username",
    "SSH Password","SSH Port","Forward DNS thru SSH","SSH Host","Use Server DNS","DoH URL",
    "DNS Transport","SSH Auth Type","SSH Private Key (B64)","SSH Key Passphrase (B64)",
    "Tor Bridge Lines (B64)","DNSTT Authoritative","Naive Port","Naive Username","Naive Password (B64)",
    "Is Locked","Lock Password Hash","Expiration Date","Allow Sharing","Bound Device ID",
    "Resolvers Hidden","Hidden Resolvers","NoizDNS Stealth","DNS Payload Size","SOCKS5 Server Port",
    "VayDNS DNSTT Compat","VayDNS Record Type","VayDNS Max Qname Len","VayDNS RPS","VayDNS Idle Timeout",
    "VayDNS Keepalive","VayDNS UDP Timeout","VayDNS Max Num Labels","VayDNS Client Id Size"]
_v21 = _v20 + ["SSH TLS Enabled","SSH TLS SNI","SSH HTTP Proxy Host","SSH HTTP Proxy Port",
    "SSH HTTP Proxy Custom Host","SSH WS Enabled","SSH WS Path","SSH WS Use TLS","SSH WS Custom Host"]
_v22 = _v21 + ["SSH Payload (B64)"]
_v24 = _v22 + ["Resolver Mode","RR Spread Count"]
_v25 = _v24 + ["VLESS UUID","VLESS Security","VLESS Transport","VLESS WS Path","CDN IP",
    "CDN Port","SNI Fragment Enabled","SNI Fragment Strategy","SNI Fragment Delay MS","Legacy SNI (Empty)"]
_v27 = _v25 + ["CH Padding Enabled","WS Header Obfuscation","WS Padding Enabled",
    "SNI Spoof TTL","Fake Decoy Host","TCP Max Seg"]
_v28 = _v27 + ["VLESS SNI"]
SLIPNET_SCHEMAS.update({
    "20": _v20, "21": _v21, "22": _v22, "23": _v24, "24": _v24,
    "25": _v25, "26": _v27, "27": _v27, "28": _v28,
})


def _slipnet_parse(plaintext):
    plaintext = plaintext.rstrip("|")
    parts = plaintext.split("|")
    if not parts or not parts[0]:
        return {"error": "empty"}
    ver = parts[0]
    schema = SLIPNET_SCHEMAS.get(ver, [])
    fields = {}
    for i, val in enumerate(parts):
        label = schema[i] if i < len(schema) else f"Field_{i}"
        fields[label] = val if val else ""
    return {"version": ver, "fields": fields}


def decrypt_slipnet_enc(payload):
    payload = payload.strip()
    if payload.startswith("slipnet-enc://"):
        payload = payload[len("slipnet-enc://"):]
    try:
        data = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:
        return {"error": f"base64: {e}"}
    if len(data) < 13:
        return {"error": "too short"}
    nonce = data[1:13]
    ct = data[13:]
    try:
        cipher = AES.new(SLIPNET_KEY, AES.MODE_GCM, nonce=nonce)
        pt = cipher.decrypt_and_verify(ct[:-16], ct[-16:])
    except Exception as e:
        return {"error": f"GCM: {e}"}
    return _slipnet_parse(pt.decode("utf-8", "ignore"))


def decrypt_slipnet_plain(payload):
    if payload.startswith("slipnet://"):
        payload = payload[len("slipnet://"):]
    try:
        data = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:
        return {"error": f"base64: {e}"}
    return _slipnet_parse(data.decode("utf-8", "ignore"))


def decrypt_slipnet_bundle(payload, password):
    payload = re.sub(r"\s+", "", payload)
    if payload.startswith("slipnet-bundle-enc://"):
        payload = payload[len("slipnet-bundle-enc://"):]
    try:
        data = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:
        return {"error": f"base64: {e}"}
    if len(data) < 1 + SLIPNET_SALT_LEN + SLIPNET_IV_LEN + 16:
        return {"error": "truncated"}
    if data[0] != 1:
        return {"error": f"version {data[0]}"}
    salt = data[1:17]
    iv = data[17:29]
    ct = data[29:]
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, SLIPNET_PBKDF2_ITER, 32)
    try:
        cipher = AES.new(derived, AES.MODE_GCM, nonce=iv)
        pt = cipher.decrypt_and_verify(ct[:-16], ct[-16:])
    except Exception as e:
        return {"error": f"wrong password or corrupted: {e}"}
    return {"plaintext": pt.decode("utf-8", "ignore")}


# ============================================================
# 2. DarkTunnel
# ============================================================
DT_KEY_256 = b"$B&E)H@McQfThWmZq4t7w!z%C*F-JaNd"
DT_KEY_192 = b"F)J@NcRfUjXn2r4u7x!A%D*G"
DT_IV = bytes.fromhex("232e39185523184a5723586242200e05")


def _dt_cfb_decrypt(data, key, iv):
    cipher = AES.new(key, AES.MODE_CFB, iv=iv, segment_size=128)
    return cipher.decrypt(data)


def _dt_b64(payload):
    payload = payload.replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    return base64.b64decode(payload)


def _dt_clean(value, key, iv):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k.startswith("Encrypted") and isinstance(v, bytes) and v:
                try:
                    out[k] = _dt_cfb_decrypt(v, key, iv)
                    continue
                except Exception:
                    pass
            out[k] = _dt_clean(v, key, iv)
        return out
    if isinstance(value, list):
        return [_dt_clean(x, key, iv) for x in value]
    return value


def _dt_normalize(value):
    if isinstance(value, dict):
        return {k: _dt_normalize(v) for k, v in value.items() if k != "Password"}
    if isinstance(value, list):
        return [_dt_normalize(x) for x in value]
    if isinstance(value, bytes):
        try:
            s = value.decode("utf-8")
            if s.startswith("{") or s.startswith("["):
                try:
                    return _dt_normalize(json.loads(s))
                except Exception:
                    pass
            return s
        except Exception:
            return list(value)
    if isinstance(value, str):
        s = value.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            fixed = re.sub(r'(:\s*)(\$[A-Za-z0-9_]+)', r'\1"\2"', s)
            try:
                return _dt_normalize(json.loads(fixed))
            except Exception:
                pass
    return value


def decrypt_darktunnel(payload):
    if not HAS_MSGPACK:
        return {"error": "msgpack غير مثبت. pip install msgpack"}
    payload = payload.strip()
    if payload.startswith("darktunnel://"):
        payload = payload[len("darktunnel://"):]
    try:
        outer_bytes = _dt_b64(payload)
    except Exception as e:
        return {"error": f"base64: {e}"}
    try:
        outer = json.loads(outer_bytes)
    except Exception as e:
        return {"error": f"json: {e}"}
    enc = outer.get("encryptedLockedConfig")
    if not enc:
        return {"error": "missing encryptedLockedConfig"}
    try:
        enc_bytes = _dt_b64(enc)
    except Exception as e:
        return {"error": f"inner base64: {e}"}
    try:
        dec_outer = _dt_cfb_decrypt(enc_bytes, DT_KEY_256, DT_IV)
        unpacked = msgpack.unpackb(dec_outer, raw=False)
    except Exception as e:
        return {"error": f"CFB256/msgpack: {e}"}
    if isinstance(unpacked, dict) and "EncryptedLockedConfig" in unpacked:
        inner = unpacked["EncryptedLockedConfig"]
        if isinstance(inner, bytes):
            try:
                dec_inner = _dt_cfb_decrypt(inner, DT_KEY_192, DT_IV)
                unpacked["EncryptedLockedConfig"] = _dt_clean(
                    msgpack.unpackb(dec_inner, raw=False), DT_KEY_192, DT_IV)
            except Exception:
                pass
    outer["encryptedLockedConfig"] = _dt_normalize(unpacked)
    return outer


# ============================================================
# 3. HTTP Custom
# ============================================================
HC_XOR_LIST = ['。','〃','〄','々','〆','〇','〈','〉','《','》',
               '「','」','『','』','【','】','〒','〓','〔','〕']
HC_VALUE_MAP = ['payload','payloadProxyURL','shouldNotWorkWithRoot','lockPayloadAndServers',
    'expiryDate','hasNotes','noteField2','sshAddress','onlyAllowOnMobileData','unlockRemoteProxy',
    'unknown','vpnAddress','sslSni','shouldConnectUsingSSH','udpgwPort','lockPayload','hasHWID',
    'hwid','noteField1','unlockUserAndPassword','sslAndPayloadMode','enablePassword','password']
HC_KEYS = ["asdgf","hc_reborn___7","hc_reborn_tester","hc_reborn_tester_5","hc_reborn_7",
    "hc_reborn_6","hc_reborn_5","hc_reborn_4","hc_reborn_3","hc_reborn_2","hc_reborn_1",
    "hc_reborn10","hc_reborn9","hc_reborn8","hc_reborn7","keY_secReaT_hc","keY_secReaT_hc1",
    "keY_secReaT_hc2","keY_secReaT_hc_reborn","keY_secReaT_hc_reborn1","keY_secReaT_hc_2",
    "keY_secReaT_hc_reborn3","keY_secReaT_hc_reborn4","keY_secReaT_hc_reborn5",
    "keY_secReaT_hc_reborn6","keY_secReaT_te4Z9","keY_secReaT_te4Z10","keY_secReaT_te4Z11",
    "keY_secReaT_e"]


def decrypt_httpcustom(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "ignore")
    if data.startswith(b"hc://"):
        data = data[5:]
    try:
        s = data.decode("utf-8")
        out = bytearray()
        for i in range(len(s)):
            v = ord(s[i]) ^ ord(HC_XOR_LIST[i % len(HC_XOR_LIST)])
            out.append(v & 0xff)
        contents = base64.b64decode(bytes(out))
    except Exception as e:
        return {"error": f"deobfuscate: {e}"}
    for key in HC_KEYS:
        try:
            k = hashlib.sha1(key.encode()).digest()[:16]
            aligned = contents[:len(contents) // 16 * 16]
            pt = AES.new(k, AES.MODE_ECB).decrypt(aligned)
            text = pt.decode("utf-8", "ignore")
        except Exception:
            continue
        if "splitConfig" in text:
            parts = text.split("[splitConfig]")
            return {"key": key, "fields": dict(zip(HC_VALUE_MAP, parts))}
    return {"error": "no key matched"}


# ============================================================
# 4. NetMod
# ============================================================
NETMOD_KEYS = [b"<n3t5yn4^n3tm0d>", b"_netsyna_netmod_", b"nicetrybuddygoon"]


def _pkcs7_unpad(data):
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and all(b == pad for b in data[-pad:]):
        return data[:-pad]
    return data


def decrypt_netmod(payload):
    payload = re.sub(r"\s+", "", payload)
    if "://" in payload:
        payload = payload.split("://", 1)[1]
    try:
        ct = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:
        return {"error": f"base64: {e}"}
    for key in NETMOD_KEYS:
        if len(key) != 16 or len(ct) % 16:
            continue
        try:
            cipher = AES.new(key, AES.MODE_ECB)
            pt = cipher.decrypt(ct)
            pt = _pkcs7_unpad(pt)
            return {"key": key.decode(), "plaintext": pt.decode("utf-8", "ignore")}
        except Exception:
            continue
    return {"error": "no netmod key matched"}


# ============================================================
# 5. HA Tunnel
# ============================================================
HAT_KEY_STR = "8515D40BD04D8C97"
HAT_KEY = SHA1.new(HAT_KEY_STR.encode()).digest()[:16]


def decrypt_hatunnel(payload):
    payload = payload.strip()
    if payload.startswith("hat://"):
        payload = payload[6:]
    try:
        ct = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:
        return {"error": f"base64: {e}"}
    if len(ct) % 16:
        return {"error": "not multiple of 16"}
    try:
        pt = AES.new(HAT_KEY, AES.MODE_ECB).decrypt(ct)
        pt = _pkcs7_unpad(pt)
        return {"plaintext": pt.decode("utf-8", "ignore")}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Classifier
# ============================================================
def classify(text):
    t = text.strip()
    if t.startswith("slipnet-enc://"): return "slipnet-enc"
    if t.startswith("slipnet-bundle-enc://"): return "slipnet-bundle"
    if t.startswith("slipnet://"): return "slipnet"
    if t.startswith("darktunnel://"): return "darktunnel"
    if t.startswith("hc://"): return "httpcustom"
    if t.startswith("nm-") or t.startswith("netmod://"): return "netmod"
    if t.startswith("hat://"): return "hatunnel"
    if "encryptedLockedConfig" in t: return "darktunnel"
    if (t.startswith("eyJ") or t.startswith("AU8")) and len(t) > 100:
        return "slipnet-enc"
    try:
        raw = t.encode("utf-8", "ignore")
        out = bytearray()
        for i in range(min(len(raw), 4096)):
            v = ord(chr(raw[i])) ^ ord(HC_XOR_LIST[i % len(HC_XOR_LIST)])
            out.append(v & 0xff)
        base64.b64decode(bytes(out)[:len(out)//4*4])
        return "httpcustom"
    except Exception:
        pass
    try:
        base64.b64decode(re.sub(r"\s+", "", t) + "=" * (-len(t) % 4))
        return "netmod"
    except Exception:
        pass
    return "unknown"


def decrypt_auto(text, password=""):
    kind = classify(text)
    if kind == "slipnet-enc":
        return {"type": kind, "result": decrypt_slipnet_enc(text)}
    if kind == "slipnet":
        return {"type": kind, "result": decrypt_slipnet_plain(text)}
    if kind == "slipnet-bundle":
        return {"type": kind, "result": decrypt_slipnet_bundle(text, password)}
    if kind == "darktunnel":
        return {"type": kind, "result": decrypt_darktunnel(text)}
    if kind == "httpcustom":
        return {"type": kind, "result": decrypt_httpcustom(text)}
    if kind == "netmod":
        return {"type": kind, "result": decrypt_netmod(text)}
    if kind == "hatunnel":
        return {"type": kind, "result": decrypt_hatunnel(text)}
    return {"type": "unknown", "error": "لم أتعرف على النوع",
            "preview": text[:120],
            "hint": "slipnet-enc:// / darktunnel:// / hc:// / nm- / hat://"}


# ============================================================
# Telegram
# ============================================================
def api(method, params=None, files=None):
    url = f"{API}/{method}"
    if files:
        boundary = "----hcb"
        body = b""
        for k, v in (params or {}).items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, (fn, fh) in files.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
            body += fh.read() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body,
              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        data = urllib.parse.urlencode(params or {}).encode()
        req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def send(chat_id, text):
    try:
        api("sendMessage", {"chat_id": chat_id, "text": text[:4000]})
    except Exception as e:
        print("send err", e)


def send_doc(chat_id, filename, data, caption=""):
    try:
        api("sendDocument", {"chat_id": chat_id, "caption": caption},
            files={"document": (filename, io.BytesIO(data))})
    except Exception as e:
        print("send_doc err", e)


def download(file_id):
    info = api("getFile", {"file_id": file_id})
    path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


# ============================================================
# main
# ============================================================
def main():
    if not BOT_TOKEN:
        print('export BOT_TOKEN="..."')
        return
    offset = 0
    print("[*] bot running — SlipNet, DarkTunnel, HTTP Custom, NetMod, HA Tunnel")
    print(f"[*] pycryptodome={HAS_AES}, msgpack={HAS_MSGPACK}")
    while True:
        try:
            upd = api("getUpdates", {"offset": offset, "timeout": 30})
        except Exception as e:
            print("poll err", e); time.sleep(3); continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            doc = msg.get("document")
            if not chat:
                continue

            # ---------- ملفات ----------
            if doc:
                try:
                    raw = download(doc["file_id"])
                    name = (doc.get("file_name") or "config").lower()

                    if name.endswith(".hc"):
                        res = {"type": "httpcustom",
                               "result": decrypt_httpcustom(raw)}
                    elif name.endswith(".slip"):
                        res = decrypt_auto(raw.decode("utf-8", "ignore"))
                    elif name.endswith(".dark"):
                        res = decrypt_auto(raw.decode("utf-8", "ignore"))
                    elif name.endswith(".nm"):
                        res = decrypt_auto(raw.decode("utf-8", "ignore"))
                    elif name.endswith(".hat"):
                        res = decrypt_auto(raw.decode("utf-8", "ignore"))
                    else:
                        res = decrypt_auto(raw.decode("utf-8", "ignore"))

                    out = json.dumps(res, indent=2, ensure_ascii=False)
                    send(chat, out[:4000])
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write(out)
                    send_doc(chat, "decrypted.json", out.encode(), "JSON كامل")
                except Exception as e:
                    send(chat, f"خطأ: {e}\n{traceback.format_exc()[-500:]}")
                continue

            # ---------- نصوص ----------
            if not text:
                send(chat, "أرسل نصاً مشفراً (slipnet-enc:// / darktunnel:// / hc:// / nm- / hat://) أو ملف .hc/.slip/.dark")
                continue
            try:
                res = decrypt_auto(text)
                out = json.dumps(res, indent=2, ensure_ascii=False)
                send(chat, out[:4000])
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(out)
                send_doc(chat, "decrypted.json", out.encode(), "JSON كامل")
            except Exception as e:
                send(chat, f"خطأ: {e}\n{traceback.format_exc()[-500:]}")


if __name__ == "__main__":
    main()
