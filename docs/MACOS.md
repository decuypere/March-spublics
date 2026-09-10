# Utiliser la veille comme une application Mac

Deux commandes une seule fois, puis plus jamais de Terminal.

---

## Étape 1 : fabriquer les applications

Dans le Terminal, depuis le dossier du projet :

```bash
./scripts/build_macos_app.sh
```

Cela crée **deux applications** dans `~/Applications` :

| Application | Ce qu'elle fait |
|---|---|
| **Veille marchés publics** | ouvre la liste des marchés dans votre navigateur |
| **Veille - Collecte** | lance une collecte immédiate et affiche le résultat |

Les deux portent la même icône, générée à la construction.

## Étape 2 : la collecte automatique

```bash
./scripts/install_launchd.sh
```

La collecte tourne alors **tous les jours à 07h00**, sans rien ouvrir.

`launchd` est l'équivalent macOS de cron, avec un avantage : si votre Mac dort
à l'heure prévue, la tâche se lance au réveil au lieu d'être sautée.

Pour une autre heure : `./scripts/install_launchd.sh --at 08:30`

---

## Au quotidien

1. **Finder → Applications** (ou Launchpad), double-cliquez sur **Veille marchés publics**
2. Le navigateur s'ouvre sur la liste
3. **Clic droit sur l'icône du Dock → Options → Garder dans le Dock**

Ensuite, un clic sur l'icône du Dock suffit.

**Pour arrêter** : Cmd+Q, ou clic droit sur l'icône du Dock → Quitter. L'icône
présente dans le Dock signifie que le serveur tourne.

---

## Premier lancement : l'avertissement macOS

macOS peut afficher *« impossible de vérifier le développeur »*. C'est normal,
l'application n'est pas signée par un compte Apple payant.

**Une seule fois** :

1. Clic droit sur l'application → **Ouvrir**
2. Dans la boîte de dialogue, cliquez sur **Ouvrir**

macOS retient ensuite votre choix.

Si l'option n'apparaît pas : **Réglages Système → Confidentialité et sécurité**,
faites défiler jusqu'au message concernant l'application, puis **Ouvrir quand même**.

---

## Vérifier que la collecte automatique tourne

```bash
launchctl list | grep be.veille.daily
```

Une ligne s'affiche : c'est installé. La première colonne est le PID (`-` quand
la tâche ne tourne pas en ce moment), la deuxième le code de sortie du dernier
lancement (`0` = tout va bien).

**Déclencher tout de suite, pour tester** :

```bash
launchctl kickstart -k gui/$(id -u)/be.veille.daily
```

**Journal** : `data/logs/launchd.log`

**Désinstaller** : `./scripts/install_launchd.sh --uninstall`

---

## Si ça ne marche pas

| Symptôme | Cause | Solution |
|---|---|---|
| Alerte « Environnement Python introuvable » | le projet n'est pas installé | `python3 -m venv .venv && .venv/bin/pip install -e .` puis reconstruire |
| L'icône rebondit puis disparaît | le serveur a planté | voir `data/logs/web.log` |
| « Le serveur n'a pas démarré » | port déjà occupé | `./scripts/build_macos_app.sh --port 8090` |
| La page est vide | aucune collecte encore faite | lancez **Veille - Collecte** |
| Rien de neuf chaque jour | tâche non chargée | `launchctl list \| grep be.veille` |

**Après une mise à jour du code** (`git pull`), reconstruisez les applications :

```bash
./scripts/build_macos_app.sh
```

Les chemins et le port sont figés dans l'application au moment de la
construction : si vous déplacez le dossier du projet ou changez le port dans
`config/config.yaml`, reconstruisez.

---

## Ce que les applications font vraiment

Aucune magie, et rien d'installé en dehors de votre dossier projet.

Une application macOS est un simple dossier. Celles-ci contiennent un script de
lancement de quelques lignes :

- **Veille marchés publics** vérifie si le serveur répond sur le port. Si oui,
  il ouvre le navigateur. Sinon, il démarre `veille web` et ouvre le navigateur
  dès que le serveur répond. Le serveur *est* le processus de l'application,
  donc quitter l'application l'arrête.
- **Veille - Collecte** exécute `veille run`, puis affiche le résumé dans une
  boîte de dialogue avec un bouton pour ouvrir la liste.

Pour inspecter :

```bash
cat ~/Applications/"Veille marches publics.app"/Contents/MacOS/launcher
```
