// Auto-uppdaterad sort_name. Replikerar derive_sort_name + parse_names_field
// så användaren ser samma resultat som backend skulle producera.
// Trigras genom att ge sort-input-fältet id = `${source_id}_sort`.

function deriveSortName(name) {
  const trimmed = name.trim();
  if (!trimmed) return '';
  const parts = trimmed.split(/\s+/);
  if (parts.length === 1) return trimmed;
  return parts[parts.length - 1] + ', ' + parts.slice(0, -1).join(' ');
}

function deriveSortField(value) {
  if (!value) return '';
  const names = value.split(/[;&]| och /).map(s => s.trim()).filter(Boolean);
  return names.map(deriveSortName).join('; ');
}

function wireSortName(sourceId) {
  const src = document.getElementById(sourceId);
  const tgt = document.getElementById(sourceId + '_sort');
  if (!src || !tgt) return;
  if (!tgt.value) tgt.value = deriveSortField(src.value);
  tgt.dataset.userEdited = tgt.value && tgt.value !== deriveSortField(src.value) ? '1' : '';
  tgt.addEventListener('input', () => { tgt.dataset.userEdited = '1'; });
  src.addEventListener('input', () => {
    if (tgt.dataset.userEdited !== '1') {
      tgt.value = deriveSortField(src.value);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  ['composer', 'arranger', 'lyricist'].forEach(wireSortName);
});
