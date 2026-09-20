# Integración con authentik

La web lee la identidad de las cabeceras que inyecta el proxy tras validar la sesión en authentik (**forward auth**).

## 1. En authentik
1. **Applications → Providers → Create → Proxy Provider**
   - Mode: **Forward auth (single application)**
   - External host: `https://inmo.tudominio.com`
2. **Applications → Create**: nombre *Inmo*, provider = el anterior. Restringí quién accede con *Policy / Group bindings*
   (sólo los grupos/usuarios que deben ver la web).
3. **Outposts**: agregá la aplicación al **embedded outpost** (o a uno propio).

## 2. Traefik (recomendado; ya está en `docker-compose.traefik.yml`)
El override define el router, el middleware `forwardauth` hacia authentik y las `authResponseHeaders`
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

## Sin authentik
Con `AUTH_DISABLED=true` la app no exige identidad. Con el override de Traefik, agregá `INMO_MIDDLEWARES=` (vacío) en `.env`
para que el router no use el forward auth. Hacelo sólo en red privada: si la URL es pública, cualquiera podrá ver y modificar todo.

## 4. Verificación
- Sin sesión, `https://inmo.tudominio.com` redirige al login de authentik.
- Con sesión, la barra superior muestra tu usuario (en pantallas anchas).
- `curl -H 'X-authentik-username: x' http://<ip-del-contenedor>:8000/` devuelve **401** (falta el secreto).
- Multiusuario: cada usuario de authentik queda registrado en cada cambio.
