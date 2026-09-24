# Integración con authentik (`AUTH_MODE=authentik`)

La web lee la identidad de las cabeceras que inyecta el proxy tras validar la sesión en authentik (**forward auth**).

## 1. En authentik
1. **Applications → Providers → Create → Proxy Provider**
   - Mode: **Forward auth (single application)**
   - External host: `https://inmo.tudominio.com`
2. **Applications → Create**: nombre *Inmo*, provider = el anterior. Restringí quién accede con *Policy / Group bindings*
   (sólo los grupos/usuarios que deben ver la web).
3. **Outposts**: agregá la aplicación al **embedded outpost** (o a uno propio).

## 2. Traefik (recomendado; `docker-compose.traefik.yml` + `docker-compose.authentik.yml`)
`docker-compose.traefik.yml` define el router y TLS; `docker-compose.authentik.yml` fija `AUTH_MODE=authentik` y define el middleware `forwardauth` hacia authentik y las `authResponseHeaders`
(`X-authentik-username`, `-email`, `-name`, `-groups`, `-uid`). Requisitos:
- Traefik y authentik en la misma red docker (`TRAEFIK_NETWORK`); `AUTHENTIK_FORWARD_AUTH_URL` debe resolver desde Traefik
  (default `http://authentik-server:9000/outpost.goauthentik.io/auth/traefik`; ajustá el nombre del servicio).
- Con outpost embebido y *single application*, el host de la app también debe enrutar `/outpost.goauthentik.io` hacia authentik
  (para el redirect de login y el callback). En el contenedor de authentik server agregá:

      traefik.http.routers.inmo-outpost.rule: Host(`inmo.tudominio.com`) && PathPrefix(`/outpost.goauthentik.io`)
      traefik.http.routers.inmo-outpost.entrypoints: websecure
      traefik.http.routers.inmo-outpost.tls.certresolver: letsencrypt
      traefik.http.routers.inmo-outpost.service: authentik   # el service que ya expone authentik en el puerto 9000
- `PROXY_SECRET`: Traefik agrega `X-Proxy-Secret` a cada pedido y la app lo exige. Así, aunque alguien alcance el contenedor
  por otra vía, no puede falsificar la identidad enviando `X-authentik-username`.
- **Extensión del navegador:** `/api/navegador/` va por un router aparte (`inmo-extension`, de mayor prioridad) **sin**
  forward auth: la extensión no tiene sesión de authentik y se autentica con su propio token (`Authorization: Bearer`,
  se genera en *Estado → Ronda por navegador*). Esas rutas no aceptan otra cosa que ese token.

## 3. nginx (alternativa)
    location / {
        auth_request        /outpost.goauthentik.io/auth/nginx;
        error_page 401 =302 https://$host/outpost.goauthentik.io/start?rd=$scheme://$http_host$request_uri;
        auth_request_set    $auth_user  $upstream_http_x_authentik_username;
        auth_request_set    $auth_email $upstream_http_x_authentik_email;
        proxy_set_header    X-authentik-username $auth_user;
        proxy_set_header    X-authentik-email    $auth_email;
        proxy_set_header    X-Proxy-Secret       "<PROXY_SECRET>";
        proxy_pass          http://inmo-web:8000;
    }
    location /outpost.goauthentik.io { proxy_pass http://authentik-server:9000/outpost.goauthentik.io; proxy_set_header Host $host; }
    location /api/navegador/ { proxy_pass http://inmo-web:8000; }   # extensión: sin authentik, usa su propio token

## 4. Verificación
- Sin sesión, `https://inmo.tudominio.com` redirige al login de authentik.
- Con sesión, la barra superior muestra tu usuario (en pantallas anchas).
- `curl -H 'X-authentik-username: x' http://<ip-del-contenedor>:8000/` devuelve **401** (falta el secreto).
- Multiusuario: cada usuario de authentik queda registrado en cada cambio.
- Extensión: `curl https://inmo.tudominio.com/api/navegador/ping` devuelve **401 en JSON** (no una redirección al login
  de authentik); con `-H 'Authorization: Bearer <token>'`, `{"ok": true, ...}`.

## Notas
- En este modo no se usan las pantallas de login, cuenta ni usuarios de la app (devuelven 404): la gestión de usuarios y grupos es de authentik.
- Si preferís no depender de authentik, usá `AUTH_MODE=basic` (login propio): ver [`docs/instalacion.md`](../docs/instalacion.md).
