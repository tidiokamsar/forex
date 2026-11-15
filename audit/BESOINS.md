# Besoins que seul le propriétaire peut satisfaire

## B-001 - Compte de démonstration chez un vrai courtier

**État :** ouvert.

**Pourquoi :** le compte actuel (114765880) est sur MetaQuotes-Demo, le serveur de démonstration de l'éditeur de la plateforme, pas un courtier. Toutes les mesures de coûts y sont étiquetées NON_REPRESENTATIF.

**Ce qu'il faut :** ouvrir un compte démo MT5 chez un courtier réellement utilisable, puis me donner le nom du serveur et le numéro du compte. Le mot de passe ne doit jamais passer par la conversation : saisissez-le vous-même dans le terminal MT5 du serveur.

**Côté code :** le changement de serveur passe par la configuration (`ASTRA_MT5_SERVER`, plus une entrée dans `SERVER_ZONES`). Ce point est traité au LOT 5.

## B-002 - Séparation réelle des limites de risque

**État :** ouvert, non bloquant.

**Pourquoi :** le fichier `/etc/astra/mt5-risk.json` appartient à root, mais l'agent a un accès root par SSH. Pour que le principe 5 soit tenu techniquement et pas seulement par discipline, il faudrait l'une de ces deux solutions :
- un compte SSH sans root pour l'agent ;
- un fichier signé par une clé que le propriétaire est seul à détenir.

**Ce qu'il faut :** votre choix entre ces deux options.
