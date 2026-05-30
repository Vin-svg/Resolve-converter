# 🎬 Resolve Converter

Petit utilitaire GUI (PySide6) pour DaVinci Resolve **gratuit sous Linux**, qui résout les deux galères de codec de la version Linux :

1. **Import → Resolve** : Resolve gratuit ne décode pas le H.264/H.265. Cet onglet transcode n'importe quelle vidéo en intermédiaire **DNxHR** (`.mov`) que Resolve avale sans broncher.
2. **Export → Compression** : Resolve gratuit ne ré-encode pas non plus en H.264/H.265, donc impossible de sortir un fichier léger directement. Cet onglet recompresse ta vidéo exportée via **NVENC** (GPU NVIDIA), avec un **aperçu avant/après en temps réel** quand tu règles la force de compression.

Interface en thème sombre type DaVinci Resolve.

---

## Prérequis

- **Linux** (testé sous Arch)
- **Python 3.10+**
- **PySide6** et **ffmpeg** (ffmpeg-git de l'AUR fonctionne aussi)
- Pour la compression GPU : un **GPU NVIDIA** avec NVENC + pilote propriétaire. Sous Linux, le H.264/H.265 ne fonctionne pas via les GPU AMD, même côté Resolve Studio.

## Installation

```bash
sudo pacman -S pyside6 ffmpeg
```

## Lancement

```bash
python resolve_converter.py
```

---

## Utilisation

### Onglet « Import → Resolve »

1. Choisis le format de sortie :
   - **Standard (8-bit)** — DNxHR HQ, pour du H.264/H.265 classique (la plupart du GoPro/DJI). Le plus léger.
   - **10-bit (log/HDR)** — DNxHR HQX, pour du D-Log, HLG ou HEVC 10-bit, afin de conserver la profondeur de couleur.
2. Glisse une ou plusieurs vidéos (n'importe quel conteneur). Chaque fichier est analysé via `ffprobe` ; ceux sans piste vidéo sont ignorés.
3. La conversion démarre automatiquement, fichier par fichier.
4. **Double-clic** sur un fichier en attente pour renommer la sortie (optionnel). Sortie en `nom_resolve.mov` à côté de la source, sans jamais écraser.

Audio converti en PCM (que Resolve apprécie). C'est aussi un meilleur workflow d'édition : le montage est plus fluide qu'en H.264.

### Onglet « Export → Compression »

1. Choisis le codec : **H.265** (plus efficace, fichiers plus petits) ou **H.264** (compatibilité maximale).
2. Glisse la vidéo exportée depuis Resolve (le `.mov`).
3. Une frame représentative est extraite et affichée dans un **comparateur avant/après** :
   - **Gauche = original**, **droite = compressé** au niveau courant.
   - Glisse la **ligne verticale blanche** sur l'image pour révéler plus ou moins de chaque côté.
4. Règle le curseur **Léger ↔ Fort**. Le côté compressé se ré-encode en direct via NVENC : tu vois les **vrais artefacts** du codec, pas une simulation.
5. Clique **⚡ Compresser la vidéo**. Sortie en `nom_compressed.mp4` (audio AAC), avec la taille finale affichée.

---

## Repère de force (quantizer NVENC)

| qp        | Rendu                              | Usage                          |
|-----------|------------------------------------|--------------------------------|
| ~18–22    | Quasi transparent, gros fichier    | Archivage, masters             |
| ~24–28    | Bon équilibre taille / qualité     | Usage courant, partage         |
| ~30–34    | Léger, artefacts visibles          | Web, brouillons                |
| ~36–40    | Très léger, dégradé                | Aperçus rapides                |

Pour du **FPV** ou tout contenu très animé, reste plutôt vers **24–28** : le mouvement masque mal les artefacts.

---

## Notes & limites

- **Pourquoi transcoder ?** Blackmagic ne paie pas les licences H.264/H.265 sur la build Linux gratuite — ni au décodage, ni à l'encodage. La version **Studio** débloque ces codecs (et le NVENC dans le Deliver), ce qui rend ce workflow inutile si tu l'achètes.
- **Aperçu = frame intra (I-frame).** C'est l'image la plus propre de la vidéo. Les frames de mouvement (P/B) seront un poil plus molles au même qp, donc le résultat final est très proche, légèrement optimiste sur les scènes très animées. C'est le compromis pour un aperçu instantané.
- **Intermédiaires DNxHR/ProRes = gros fichiers.** Normal : ils sont peu compressés, c'est ce qui les rend fluides au montage. Prévois l'espace disque.
- **NVENC = NVIDIA uniquement.** Pas de repli CPU intégré pour l'instant (peut être ajouté avec `libx265`/`libx264`).

## Dépannage

- **« échec encodage — NVENC dispo ? »** dans l'aperçu : le pilote NVIDIA ou NVENC n'est pas accessible. Vérifie `ffmpeg -encoders | grep nvenc`.
- **« Introuvable : ffmpeg / ffprobe »** : installe `ffmpeg`.
- **L'audio d'un MKV ne passe pas dans Resolve** (AC3/AAC) : ni Resolve gratuit ni Studio ne décodent l'AAC ; l'onglet Import ré-encode déjà l'audio en PCM, donc passe par lui.

---

## Structure du projet

Un seul fichier : `resolve_converter.py`. Aucune config, aucun état persistant ; les fichiers temporaires d'aperçu sont nettoyés à la fermeture.
