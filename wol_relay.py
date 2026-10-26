#!/usr/bin/env python3
"""
Relé Wake-on-LAN do Remoto GF.

Roda num aparelho que fica SEMPRE LIGADO na rede de casa (o servidor caseiro, um Raspberry Pi,
um notebook velho...) e que também está no Tailscale. Fora de casa, o app do celular pede para
este relé, pelo Tailscale, e o relé manda o "pacote mágico" na rede de casa para ligar o PC.

Uso:
    python3 wol_relay.py            (porta 8766)
    python3 wol_relay.py --porta 8766

Na primeira vez ele cria wol_relay.json com um token. Copie o token para o app:
Configurações > Fora de casa > Relé para ligar o PC.

Só usa a biblioteca padrão do Python 3 (Linux, Windows ou macOS).
"""
import argparse
import ipaddress
import json
import os
import secrets
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wol_relay.json")


def carregar_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(18)
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


def broadcasts():
    """Endereços de broadcast das redes locais (além do 255.255.255.255)."""
    out = {"255.255.255.255"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        out.add(str(ipaddress.ip_network(ip + "/24", strict=False).broadcast_address))
    except Exception:
        pass
    return out


def pacote_magico(mac):
    limpo = "".join(c for c in mac if c in "0123456789abcdefABCDEF")
    if len(limpo) != 12:
        raise ValueError("MAC inválido")
    return b"\xff" * 6 + bytes.fromhex(limpo) * 16


def acordar(mac):
    pkt = pacote_magico(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            for dest in broadcasts():
                for porta in (9, 7):
                    try:
                        s.sendto(pkt, (dest, porta))
                    except OSError:
                        pass
            time.sleep(0.2)


def pc_online(ip, porta=8765):
    try:
        with socket.create_connection((ip, porta), timeout=1.5):
            return True
    except OSError:
        return False


class Handler(BaseHTTPRequestHandler):
    cfg = {}

    def log_message(self, fmt, *a):
        sys.stderr.write("[relé] " + (fmt % a) + "\n")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json(200, {})

    def _params(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if self.command == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                q.update(json.loads(self.rfile.read(n) or b"{}"))
            except Exception:
                pass
        return q

    def _rota(self):
        p = urlparse(self.path).path.rstrip("/")
        q = self._params()
        if p == "/ping":
            return self._json(200, {"app": "Relé Remoto GF", "ok": True})
        if not secrets.compare_digest(str(q.get("t", "")), self.cfg["token"]):
            time.sleep(1)  # atrasa quem tenta adivinhar o token
            return self._json(403, {"erro": "Token do relé incorreto."})
        if p == "/wol":
            try:
                acordar(str(q.get("mac", "")))
            except ValueError as e:
                return self._json(400, {"erro": str(e)})
            return self._json(200, {"ok": True, "msg": "Pacote mágico enviado na rede de casa."})
        if p == "/status":
            ip = str(q.get("ip", ""))
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                return self._json(400, {"erro": "IP inválido"})
            return self._json(200, {"online": pc_online(ip, int(q.get("porta") or 8765))})
        return self._json(404, {"erro": "Rota não existe"})

    do_GET = _rota
    do_POST = _rota


def main():
    ap = argparse.ArgumentParser(description="Relé Wake-on-LAN do Remoto GF")
    ap.add_argument("--porta", type=int, default=8766)
    a = ap.parse_args()
    Handler.cfg = carregar_cfg()
    print(f"Relé Wake-on-LAN do Remoto GF na porta {a.porta}")
    print(f"Token (copie para o app): {Handler.cfg['token']}")
    ThreadingHTTPServer(("0.0.0.0", a.porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
