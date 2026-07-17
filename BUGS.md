# PINKWARD — Rapport de QA / bugs connus

**Testé le** : 2026‑07‑17
**Cible** : https://pinkward.lol (déploiement live)
**Contexte du test** : visiteur **anonyme** (mode public lecture seule), assets `?v=19`.
**Méthode** : parcours de bout en bout de toutes les vues via inspection DOM / réseau / console
(la capture d'écran de l'outil était indisponible pendant la session).
**Non testé volontairement** : les **jobs d'ingestion admin** (Riot/Lolpros/Leaguepedia/tournois)
et toute action nécessitant un compte admin.

## ✅ Ce qui fonctionne (vérifié)

- **HTTPS + déploiement** : site live, certificat OK, app chargée, routing hash opérationnel.
- **Ladder** : chargement, tri par CSS, **pagination** (page 2 = rangs 51‑100), **filtre région**
  (KR testé → 300 aggregates), **quick‑pills**, **groupement de comptes** (badges + « 31 alt
  account(s) collapsed »), **Export CSV** (blob client‑side).
- **Profil joueur** : header, CSS trend, activity, radar, aggregate stats, champion pool,
  recent matches, matchups (vs champion), score breakdown, smurf signals. **Match deep‑dive**
  (modal + 3 charts + rosters + boutons JSON/External/.rofl) OK.
- **Champions** (240 lignes), **Compare** (recherche interne → 3 suggestions OK, empty state OK),
  **Patch impact** (Δ CSS, 34 lignes), **Teams** (roster G2 OK), **Help**, **Glossaire**.
- **Contrôle d'accès anonyme** : reads publics → 200, `/watchlist` & `/alerts` → 401 ;
  nav Watchlist/Alerts/Admin masquées ; bounce des deep‑links `#/watchlist` `#/alerts` `#/admin`
  vers le ladder ; bouton « Sign in » affiché, contrôles d'écriture (star, note, smurf) masqués
  (`role-viewer`).

---

## 🔴 Élevé

### 1. La recherche globale du topbar est morte (aucun handler)
- **Où** : [index.html:125‑127](frontend/index.html#L125) (`#global-search` + `#global-suggest` + badge `⌘K`).
- **Constat** : taper dans la barre « Search players, teams, matches… » n'affiche **aucune
  suggestion**, et le raccourci **⌘K** ne fait rien. Aucun `addEventListener` n'est câblé sur
  `#global-search`/`#global-suggest` dans tout le JS, et il n'existe aucun handler `keydown`
  pour ⌘K.
- **Preuve** : l'API `/players/search?q=faker` renvoie bien 3 résultats, et la recherche
  **interne de Compare** ([views.js:991](frontend/js/views.js#L991)) fonctionne — c'est
  uniquement la barre du topbar qui n'est pas branchée.
- **Impact** : élément le plus visible de l'en‑tête, donne l'impression d'être cassé.
- **Piste de correction** : réutiliser la logique de suggestion de Compare/Player sur
  `#global-search` (debounce → `/players/search` → rendu dans `#global-suggest` → clic =
  `setView('player', puuid)`), et ajouter un handler `keydown` (⌘/Ctrl+K → focus).

### 2. Layout mobile cassé (media queries obsolètes)
- **Où** : [style.css:1685+](frontend/style.css#L1685), [style.css:1949](frontend/style.css#L1949),
  et l'absence de règle responsive sur `.app-grid` ([style.css:130](frontend/style.css#L130)).
- **Constat** : toutes les `@media` ciblent l'**ancien markup** d'avant la refonte
  (`header`, `nav`, `.brand`, `.user-menu`, `.filters`) qui **n'existe plus**. La coquille
  actuelle (`.app-grid`, `.sidebar`, `.topbar`, `.nav-item`, `.filter-card`) n'a **aucune**
  règle responsive.
- **Preuve** : en viewport étroit, `.app-grid` reste en `grid-template-columns: 236px 139px`
  → la sidebar garde 236 px et la colonne de contenu est écrasée à ~139 px. Pas de menu
  hamburger ni de repli de sidebar.
- **Impact** : app pratiquement inutilisable sur mobile/tablette.
- **Piste de correction** : ajouter des `@media` sur le nouveau layout (replier `.sidebar`
  en barre horizontale scrollable ou derrière un toggle, passer `.app-grid` en une seule
  colonne sous ~900 px) ; retirer les media queries mortes qui ciblent l'ancien markup.

---

## 🟠 Teams (signalé + corrigé dans cette passe)

### 10. Liste d'équipes du frontend périmée (BDS, KOI) — ✅ corrigé
- **Où** : [views.js:571](frontend/js/views.js#L571) (quick‑pills codées en dur).
- **Constat** : la liste contenait **BDS** et **KOI**, qui ont **rebrandé**. La base est déjà
  à jour (`/teams/BDS` → 404, `/teams/SHFT` → « Shifters » ; `/teams/KOI` → 404, `/teams/NAVI`
  → « Natus Vincere »). Cliquer BDS/KOI tombait donc sur un 404.
- **Correctif** : liste mise à jour → `G2, FNC, KC, MKOI, TH, SHFT, SK, VIT, GX, NAVI`.

### 11. Roster : mauvais joueurs / coachs / rôles en double / CSS 0 — ✅ corrigé
- **Où** : `team_detail` [tournaments.py:945+](backend/app/routers/tournaments.py#L945).
- **Constat** : le roster était construit depuis **toutes** les lignes `PlayerMeta` taggées à
  l'équipe (matching par tag SoloQ). Conséquences :
  - Des **comptes SoloQ au hasard** taggés « SK … » remontaient à la place des vrais joueurs
    (ex. SK affichait « Expedition 28 / iladra / Shun » au lieu de Skeanz / LIDER…).
  - Des **coachs / ex‑joueurs** apparaissaient comme titulaires : un ex‑jungler devenu coach
    garde son ancien rôle de joueur dans les données (`meta.role = role or meta.role`,
    [lolpros.py:351](backend/app/services/lolpros.py#L351)) → **ex. « Kesha » (coach) affiché en JGL**.
  - Rôles en double (2 JGL, 2 MID, 2 SUP), rôles manquants, comptes smurf à CSS 0.
- **Cause racine** : `PlayerMeta` (SoloQ/Lolpros) ne sait pas qui est titulaire vs staff vs
  smurf. La table **`current_lec_roster`** contient pourtant les **5 titulaires officiels**
  par équipe (source lolesports, propre), mais l'endpoint l'**ignorait**.
- **Correctif** : le roster est désormais construit **depuis `current_lec_roster`** (5 titulaires
  officiels, un par rôle, sans coach ni smurf), enrichi du **CSS/rang SoloQ** quand on peut
  relier le titulaire à un compte tracké (par `lolesports_id`, puis par nom). Fallback vers
  l'ancien scan `PlayerMeta` (dédupliqué par pro puis par rôle, `is_retired == False`) pour les
  équipes hors LEC. Vérifié en local sur les 10 équipes : **5 titulaires corrects, 0 doublon,
  0 coach** (SK = Wunder/Skeanz/LIDER/Jopa/Mikyx).
- **Limite restante (données)** : un titulaire sans compte SoloQ tracké s'affiche avec son nom
  + rôle mais **CSS « — »** (ex. Jopa, Mikyx). Le lier nécessiterait un re‑sync pro‑identité
  (job admin).

---

## 🟡 Mineur / polish

### 3. Requêtes 401 inutiles en anonyme
- **Où** : `refreshWatchedSet()` [views.js:6](frontend/js/views.js#L6) appelée à chaque
  chargement de ladder ([views.js:69](frontend/js/views.js#L69)) → `GET /watchlist` **401** ;
  et `GET /notes/{puuid}` [player.js:671](frontend/js/player.js#L671) à chaque ouverture de
  profil → **401**.
- **Constat** : ces appels partent même sans session (le token est absent). Ils sont bien
  attrapés (try/catch), donc **pas de casse visible**, mais génèrent du bruit réseau + des
  round‑trips inutiles à chaque page.
- **Piste** : court‑circuiter côté client si `!currentUser()` (mettre un set vide / notes vides
  sans requêter).

### 4. La carte « Scout notes » s'affiche vide en lecture seule
- **Où** : section notes du profil ([player.js:671+](frontend/js/player.js#L671)).
- **Constat** : le champ de saisie est bien masqué (`display:none` via `role-viewer`), mais la
  **carte entière « Scout notes » reste visible** (block) et vide pour un anonyme.
- **Piste** : masquer toute la carte en `role-viewer`, ou afficher un « Connectez‑vous pour
  ajouter des notes ».

### 5. Accord du badge de comptes : « +1 accounts »
- **Où** : [views.js:157](frontend/js/views.js#L157) — `+${row._account_count - 1} accounts`.
- **Constat** : quand il n'y a qu'un compte alternatif, on lit **« +1 accounts »** (pluriel
  incorrect).
- **Piste** : pluraliser (`account` / `accounts` selon `n === 1`).

### 6. Cloche de notifications décorative
- **Où** : bouton « Notifications » du topbar + `.notif-dot`.
- **Constat** : aucun handler câblé, et le **point rouge est affiché en permanence**
  (laisse croire à des notifications non lues).
- **Piste** : soit brancher une vraie fonctionnalité, soit retirer la pastille / le bouton.

### 7. Texte « internal use only » incohérent avec l'accès public
- **Où** : vue **Help** (« Everything below is for internal use only — not for public sharing »).
- **Constat** : le site est désormais **public en lecture seule**, ce message n'est plus exact.
- **Piste** : reformuler pour refléter l'accès public read‑only.

---

## 🔵 À confirmer (décisions, pas des bugs)

### 8. Site public mais `noindex` + `robots: Disallow`
- `robots.txt` (`Disallow: /`) et `<meta name="robots" content="noindex, nofollow">` sont
  toujours actifs. Volontaire (clé **Riot Personal Key**), mais à confirmer si tu veux que le
  site soit **référencé**. ⚠️ Rappel : les conditions d'une **Personal API Key** restreignent
  l'usage public — une **Production Key** serait plus carrée pour un site accessible à tous.

### 9. Config `SCOUTING_API_BASE` obsolète
- **Où** : script inline [index.html:38‑45](frontend/index.html#L38).
- Référence encore un tunnel Cloudflare (`*.trycloudflare.com`) pour un hébergement
  `*.vercel.app` qui n'est plus d'actualité. Sans effet sur `pinkward.lol` (même origine),
  mais code mort à nettoyer.

---

## Résumé

| # | Sévérité | Sujet |
|---|----------|-------|
| 1 | 🔴 Élevé | Recherche globale du topbar + ⌘K non branchés |
| 2 | 🔴 Élevé | Layout mobile cassé (media queries obsolètes) |
| 10 | ✅ Corrigé | Liste d'équipes périmée (BDS→SHFT, KOI→NAVI) |
| 11 | ✅ Corrigé | Roster : rôles en double / comptes alternatifs |
| 3 | 🟡 Mineur | 401 inutiles (`/watchlist`, `/notes`) en anonyme |
| 4 | 🟡 Mineur | Carte « Scout notes » vide affichée en lecture seule |
| 5 | 🟡 Mineur | Accord « +1 accounts » |
| 6 | 🟡 Mineur | Cloche de notifs décorative + pastille permanente |
| 7 | 🟡 Mineur | Texte « internal use only » vs accès public |
| 8 | 🔵 Décision | `noindex`/robots + conditions Riot Personal Key |
| 9 | 🔵 Nettoyage | Config `SCOUTING_API_BASE` obsolète |

Aucun **crash** ni erreur console bloquante détecté(e). Les deux points 🔴 (recherche globale
morte, mobile cassé) sont les plus impactants pour l'expérience.
