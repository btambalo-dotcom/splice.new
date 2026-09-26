
window.I18N = window.I18N || {};
(function(){
  const dict = {
    pt: {
      production: "Produção", my_maps: "Meus Mapas", entry: "Lançar", photo_entry: "Lançar (foto)", expenses: "Despesas", settings: "Configurações",
      invoices: "Invoices", users: "Usuários", logout: "Sair", map_admin: "Administração do mapa interativo para este projeto.",
      back_project: "← Voltar para o projeto", search_device_placeholder: "Buscar device (ex: FT61, RN51E)...", search: "Buscar",
      show_all: "Mostrar tudo", tests_report: "Relatório testes", import_devices_kmz: "Importar dispositivos via KMZ", import_kmz: "Importar KMZ",
      kmz_help: "O sistema vai criar ou atualizar os dispositivos deste mapa com base nos pontos do arquivo KMZ.", build_network: "Montar rede automática",
      pon_colors: "Cores dos PONs", pon_colors_help: "Os dispositivos agora usam cor por PON. Cada PON recebe uma cor automática para facilitar a leitura da rede no mapa.",
      refresh_pons: "Atualizar lista de PONs", manual_add: "Adicionar dispositivo manualmente", device_name: "Nome do dispositivo", latitude: "Latitude",
      longitude: "Longitude", device_info: "Informações do dispositivo", add_device: "Adicionar dispositivo", type: "Tipo", address: "Endereço",
      pon: "PON", section: "Seção", splitter: "Splitter", from: "FROM", out: "OUT", open_device: "Abrir dispositivo", copy_info: "Copiar info",
      move: "Mover", google_maps: "Google Maps", edit_on_map: "Editar no mapa", launch_test: "Lançar teste", no_name: "Sem nome",
      admin_language: "Idioma", portuguese: "Português", english: "English"
    },
    en: {
      production: "Production", my_maps: "My Maps", entry: "Entry", photo_entry: "Photo Entry", expenses: "Expenses", settings: "Settings",
      invoices: "Invoices", users: "Users", logout: "Logout", map_admin: "Interactive map administration for this project.",
      back_project: "← Back to project", search_device_placeholder: "Search device (e.g. FT61, RN51E)...", search: "Search",
      show_all: "Show all", tests_report: "Tests report", import_devices_kmz: "Import devices via KMZ", import_kmz: "Import KMZ",
      kmz_help: "The system will create or update this map's devices based on the points in the KMZ file.", build_network: "Build automatic network",
      pon_colors: "PON colors", pon_colors_help: "Devices now use color by PON. Each PON gets an automatic color to make the network easier to read on the map.",
      refresh_pons: "Refresh PON list", manual_add: "Add device manually", device_name: "Device name", latitude: "Latitude",
      longitude: "Longitude", device_info: "Device information", add_device: "Add device", type: "Type", address: "Address",
      pon: "PON", section: "Section", splitter: "Splitter", from: "FROM", out: "OUT", open_device: "Open device", copy_info: "Copy info",
      move: "Move", google_maps: "Google Maps", edit_on_map: "Edit on map", launch_test: "Launch test", no_name: "No name",
      admin_language: "Language", portuguese: "Portuguese", english: "English"
    }
  };
  function getLang(){ return localStorage.getItem('ui_lang') || 'pt'; }
  function setLang(lang){ localStorage.setItem('ui_lang', lang); applyTranslations(); document.documentElement.lang = lang === 'en' ? 'en' : 'pt-br'; }
  function t(key){ const lang=getLang(); return (dict[lang] && dict[lang][key]) || dict.pt[key] || key; }
  function applyTranslations(){
    document.querySelectorAll('[data-i18n]').forEach(el=>{ const key=el.getAttribute('data-i18n'); el.textContent=t(key);});
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el=>{ const key=el.getAttribute('data-i18n-placeholder'); el.setAttribute('placeholder', t(key));});
    const sel=document.getElementById('lang-switcher'); if(sel) sel.value=getLang();
  }
  document.addEventListener('DOMContentLoaded', applyTranslations);
  window.t = t; window.setUILanguage = setLang;
})();
