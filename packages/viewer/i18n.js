// Translations. English is the reference; add a language by adding a dictionary with the same keys.
// In HTML: data-i18n="key" (text), data-i18n-title="key" (tooltip). In JS: t('key', {var}).
// Plurals: key_one / key_other, chosen from vars.n. Android app: keyAndroid, when it exists, replaces key.
'use strict';

window.I18N = {
  en: {
    camera: 'Camera', folder: 'Folder',
    connect: 'Connect', disconnect: 'Disconnect',
    connecting: 'Connecting… (approve the macOS prompt if it appears)', disconnecting: 'Disconnecting…',
    connectedTo: 'Connected to {ssid}', wifi: 'Wi-Fi: {ssid}', none: 'none',
    serverDown: 'local server stopped?', failed: 'Failed: {err}',
    waitingCamera: 'Waiting for the camera… {s}s (turn it on and enable its Wi-Fi; retrying for 90 s)',
    clickConnect: 'Click “Connect” to join the camera’s Wi-Fi.',
    joining: 'Joining the camera’s Wi-Fi… (macOS may ask for an administrator account)',
    leaving: 'Switching back to your usual Wi-Fi… Ratings are already saved.',
    disconnected: 'Disconnected. Ratings are saved in ratings.json.',
    change: 'Change…', writeFile: 'write ratings into files',
    writeFileTitle: 'Also writes xmp:Rating into the DNG (Lightroom: Metadata → Read Metadata from File)',
    pickFolder: 'Choose a photo folder',
    chooseFolderMsg: 'Choose a folder in the macOS window (it may be hidden behind the browser).',
    chooserBusy: 'The folder picker is already open (behind the browser?)', noFolder: 'No folder chosen.',
    chooseFolderMsgAndroid: 'Choose a folder (memory card in a USB-C reader, or a phone folder) then tap “Use this folder”.',
    chooserBusyAndroid: 'The folder picker is already open.',
    allFolders: 'All folders ({n} photos)', folderTitle: 'Folders on the card (or sub-folders)',
    listing: 'Reading the photo list…', error: 'Error: {err}',
    noPhotosGroup: 'No photos in this folder.', noPhotos: 'No photos.',
    loading: 'Loading…', preloaded: 'preloaded', loadingFull: 'Loading full resolution…', zoomFailed: 'Zoom failed: {err}',
    ratingFromFile: 'Rating read from the file',
    ratedCount_one: '{n} rating in ratings.json', ratedCount_other: '{n} ratings in ratings.json',
    saveError: 'Could not save the rating: {err}', fileNotWritten: '{name}: rating not written to the file ({err})',
    fileWritten: '{name}: {r} written to the file', cleared: 'rating cleared',
    noMoreUnrated: 'No unrated photo after this one',
    autoAdv: 'go to next photo after rating',
    shortcuts: '← → navigate · 1–5 rate · 0 clear · U next unrated · A auto tone · G grid (Enter opens, + − size) · B background · click photo: 100% zoom (folder mode), Esc to exit',
    gridTitle: 'Contact sheet (G)',
    settings: 'Settings', language: 'Language', auto: 'Automatic', theme: 'Theme', dark: 'Dark', light: 'Light',
    photoBg: 'Photo background', bgTheme: 'Same as theme', black: 'Black', grey: 'Grey', white: 'White',
    bgToast: 'Background: {bg}', close: 'Close',
    download: 'Download', downloadTitle: 'Download photos', copyTitle: 'Copy photos',
    photos: 'Photos', f5: '5★ only', f4: '4★ and up', f3: '3★ and up', f1: 'All rated photos', fcur: 'Current photo only',
    fall: 'All photos in view',
    inView: 'Among the {n} photos shown ({scope}).', dest: 'Destination',
    writeRating: 'Write ratings into the downloaded files (Lightroom shows the stars on import)',
    count_one: '1 photo', count_other: '{n} photos', start: 'Download', startCopy: 'Copy', cancel: 'Cancel',
    camNote: 'Original files (~45 MB each over the camera Wi-Fi). You can keep browsing, a bit more slowly.',
    dlProgress: '{done}/{total} · {name} {pct}%', dlDone: 'Done: {ok} downloaded, {skip} already there, {fail} failed',
    dlCancelled: 'Download cancelled', dlBusy: 'A download is already running', dlNothing: 'No photo matches.',
    reveal: 'Show in Finder',
    androidDest: 'Photos › Pictures/Pentax Cull', revealAndroid: 'Open',
    connectingAndroid: 'Connecting… (approve the Android prompt)',
    joiningAndroid: 'Joining the camera’s Wi-Fi… Choose it in the Android window. Mobile data stays on.',
    leavingAndroid: 'Leaving the camera’s Wi-Fi… Ratings are already saved.',
    disconnectedAndroid: 'Disconnected. Ratings are saved on the phone.',
    ratingsTitle: 'Ratings', exportRatings: 'Export ratings…',
    exportNote: 'Saves a dated copy of the ratings (e.g. into Dropbox). On the Mac, choose that folder as the inbox: ratings are merged automatically.',
    exported: 'Exported: {name} ({n} ratings)', exportFailed: 'Export failed: {err}',
    inboxTitle: 'Ratings from the phone (inbox folder)',
    inboxHelp: 'Choose the folder where the phone saves its exports (e.g. in Dropbox). New files are merged while this app runs.',
    inboxEmpty: 'No export merged yet. Checked at start and every 30 s.',
    inboxLast: 'Last merged: {file} ({added} added, {updated} updated) on {at}',
    imported: 'Ratings from {file}: {added} added, {updated} updated',
    autoTone: 'Auto', autoToneTitle: 'Auto tone: brightens / balances the preview (display only, the file is not changed) — key A',
    autoToneOn: 'Auto tone on (display only)', autoToneOff: 'Auto tone off',
    camSsid: 'Camera Wi-Fi name', camPass: 'Camera Wi-Fi password', passSaved: '•••••• (saved)', saved: 'Saved',
    cache: 'Preview cache (max size)', cacheClear: 'Clear cache', cacheCleared: 'Cache cleared',
    cacheUsage_one: '{size} used ({n} file) / {limit}', cacheUsage_other: '{size} used ({n} files) / {limit}',
    removableNoWrite: 'Memory card or external disk: ratings are not written into its files (tick the box to enable)',
  },
  fr: {
    camera: 'Appareil', folder: 'Dossier',
    connect: 'Connexion', disconnect: 'Déconnexion',
    connecting: 'Connexion… (valider la fenêtre macOS si elle apparaît)', disconnecting: 'Déconnexion…',
    connectedTo: 'Connecté à {ssid}', wifi: 'Wi-Fi : {ssid}', none: 'aucun',
    serverDown: 'serveur local arrêté ?', failed: 'Échec : {err}',
    waitingCamera: 'En attente de l’appareil… {s} s (allumez-le et activez son Wi-Fi ; nouvel essai pendant 90 s)',
    clickConnect: 'Cliquez sur « Connexion » pour rejoindre le Wi-Fi de l’appareil.',
    joining: 'Connexion au Wi-Fi de l’appareil… (macOS peut demander un compte administrateur)',
    leaving: 'Retour sur le Wi-Fi habituel… Les notes sont déjà enregistrées.',
    disconnected: 'Déconnecté. Les notes sont enregistrées dans ratings.json.',
    change: 'Changer…', writeFile: 'écrire la note dans les fichiers',
    writeFileTitle: 'Écrit aussi xmp:Rating dans le DNG (Lightroom : Métadonnées → Lire les métadonnées du fichier)',
    pickFolder: 'Choisir un dossier de photos',
    chooseFolderMsg: 'Choisissez un dossier dans la fenêtre macOS (elle peut être cachée derrière le navigateur).',
    chooserBusy: 'La fenêtre de choix de dossier est déjà ouverte (derrière le navigateur ?)', noFolder: 'Aucun dossier choisi.',
    chooseFolderMsgAndroid: 'Choisissez un dossier (carte mémoire dans un lecteur USB-C, ou dossier du téléphone) puis touchez « Utiliser ce dossier ».',
    chooserBusyAndroid: 'Le choix de dossier est déjà ouvert.',
    allFolders: 'Tous les dossiers ({n} photos)', folderTitle: 'Dossiers de la carte (ou sous-dossiers)',
    listing: 'Lecture de la liste des photos…', error: 'Erreur : {err}',
    noPhotosGroup: 'Aucune photo dans ce dossier.', noPhotos: 'Aucune photo.',
    loading: 'Chargement…', preloaded: 'préchargée', loadingFull: 'Chargement de la pleine définition…', zoomFailed: 'Zoom impossible : {err}',
    ratingFromFile: 'Note lue dans le fichier',
    ratedCount_one: '{n} note dans ratings.json', ratedCount_other: '{n} notes dans ratings.json',
    saveError: 'Erreur d’enregistrement : {err}', fileNotWritten: '{name} : note non écrite dans le fichier ({err})',
    fileWritten: '{name} : {r} écrite dans le fichier', cleared: 'note effacée',
    noMoreUnrated: 'Plus de photo non notée après celle-ci',
    autoAdv: 'passer à la suivante après notation',
    shortcuts: '← → naviguer · 1–5 noter · 0 effacer · U prochaine non notée · A tons auto · G grille (Entrée ouvre, + − taille) · B fond · clic sur la photo : zoom 100 % (mode dossier), Échap pour sortir',
    gridTitle: 'Planche contact (G)',
    settings: 'Réglages', language: 'Langue', auto: 'Automatique', theme: 'Thème', dark: 'Sombre', light: 'Clair',
    photoBg: 'Fond de la photo', bgTheme: 'Celui du thème', black: 'Noir', grey: 'Gris', white: 'Blanc',
    bgToast: 'Fond : {bg}', close: 'Fermer',
    download: 'Télécharger', downloadTitle: 'Télécharger des photos', copyTitle: 'Copier des photos',
    photos: 'Photos', f5: '5★ seulement', f4: '4★ et plus', f3: '3★ et plus', f1: 'Toutes les photos notées', fcur: 'Photo affichée seulement',
    fall: 'Toutes les photos affichées',
    inView: 'Parmi les {n} photos affichées ({scope}).', dest: 'Destination',
    writeRating: 'Écrire la note dans les fichiers téléchargés (Lightroom affiche les étoiles dès l’import)',
    count_one: '1 photo', count_other: '{n} photos', start: 'Télécharger', startCopy: 'Copier', cancel: 'Annuler',
    camNote: 'Fichiers originaux (~45 Mo chacun par le Wi-Fi de l’appareil). Vous pouvez continuer à naviguer, un peu plus lentement.',
    dlProgress: '{done}/{total} · {name} {pct} %', dlDone: 'Terminé : {ok} téléchargée(s), {skip} déjà présente(s), {fail} en échec',
    dlCancelled: 'Téléchargement annulé', dlBusy: 'Un téléchargement est déjà en cours', dlNothing: 'Aucune photo ne correspond.',
    reveal: 'Afficher dans le Finder',
    androidDest: 'Photos › Pictures/Pentax Cull', revealAndroid: 'Ouvrir',
    connectingAndroid: 'Connexion… (validez la fenêtre Android)',
    joiningAndroid: 'Connexion au Wi-Fi de l’appareil… Choisissez-le dans la fenêtre Android. Les données mobiles restent actives.',
    leavingAndroid: 'Déconnexion de l’appareil… Les notes sont déjà enregistrées.',
    disconnectedAndroid: 'Déconnecté. Les notes sont enregistrées sur le téléphone.',
    ratingsTitle: 'Notes', exportRatings: 'Exporter les notes…',
    exportNote: 'Enregistre une copie datée des notes (par exemple dans Dropbox). Sur le Mac, choisissez ce dossier comme dossier de réception : les notes sont fusionnées automatiquement.',
    exported: 'Exporté : {name} ({n} notes)', exportFailed: 'Échec de l’export : {err}',
    inboxTitle: 'Notes du téléphone (dossier de réception)',
    inboxHelp: 'Choisissez le dossier où le téléphone enregistre ses exports (par exemple dans Dropbox). Les nouveaux fichiers sont fusionnés tant que l’appli tourne.',
    inboxEmpty: 'Aucun export fusionné pour l’instant. Vérifié au démarrage puis toutes les 30 s.',
    inboxLast: 'Dernière fusion : {file} ({added} ajoutée(s), {updated} mise(s) à jour) le {at}',
    imported: 'Notes de {file} : {added} ajoutée(s), {updated} mise(s) à jour',
    autoTone: 'Auto', autoToneTitle: 'Tons auto : éclaircit / équilibre l’aperçu (affichage seulement, le fichier n’est pas modifié) — touche A',
    autoToneOn: 'Tons auto activés (affichage seulement)', autoToneOff: 'Tons auto désactivés',
    camSsid: 'Nom du Wi-Fi de l’appareil', camPass: 'Mot de passe du Wi-Fi de l’appareil', passSaved: '•••••• (enregistré)', saved: 'Enregistré',
    cache: 'Cache des aperçus (taille max.)', cacheClear: 'Vider le cache', cacheCleared: 'Cache vidé',
    cacheUsage_one: '{size} utilisés ({n} fichier) / {limit}', cacheUsage_other: '{size} utilisés ({n} fichiers) / {limit}',
    removableNoWrite: 'Carte mémoire ou disque externe : les notes ne sont pas écrites dans ses fichiers (cochez la case pour l’activer)',
  },
};

window.i18n = (() => {
  let lang = 'en';
  const resolve = (pref) => {
    if (pref && pref !== 'auto' && I18N[pref]) return pref;
    const nav = (navigator.languages || [navigator.language || 'en']).map(l => l.slice(0, 2));
    return nav.find(l => I18N[l]) || 'en';
  };
  const t = (key, vars = {}) => {
    const d = I18N[lang], en = I18N.en;
    if (window.AndroidBridge && (d[key + 'Android'] || en[key + 'Android'])) key += 'Android';
    const k = 'n' in vars ? `${key}_${vars.n === 1 ? 'one' : 'other'}` : key;
    let s = d[k] ?? d[key] ?? en[k] ?? en[key] ?? key;
    return s.replace(/\{(\w+)\}/g, (m, v) => (v in vars ? vars[v] : m));
  };
  const apply = (root = document) => {
    document.documentElement.lang = lang;
    for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const el of root.querySelectorAll('[data-i18n-title]')) el.title = t(el.dataset.i18nTitle);
  };
  return { t, apply, set: (pref) => { lang = resolve(pref); apply(); return lang; }, get lang() { return lang; } };
})();
window.t = window.i18n.t;
