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

  const textMap = {
    en: {
      "SPLICER · Produção": "SPLICER · Production",
      "Registros no banco": "Records in database",
      "Valor total acumulado (USD)": "Total accumulated value (USD)",
      "Formato aceito": "Accepted format",
      "Filtros": "Filters",
      "Empresa": "Company",
      "Todas": "All",
      "Todos": "All",
      "Splicer / Usuário": "Splicer / User",
      "Map": "Map",
      "Buscar por dispositivo": "Search by device",
      "Data início": "Start date",
      "Data fim": "End date",
      "Aplicar filtros": "Apply filters",
      "PDF com valores": "PDF with values",
      "PDF sem valores": "PDF without values",
      "Lançamento manual": "Manual entry",
      "Novo lançamento": "New entry",
      "Editar lançamento": "Edit entry",
      "Selecione a empresa": "Select company",
      "Selecione a empresa primeiro": "Select company first",
      "Tipo / Dispositivo": "Type / Device",
      "Nome do dispositivo": "Device name",
      "Splices": "Splices",
      "Data": "Date",
      "Fotos do dispositivo (opcional)": "Device photos (optional)",
      "Como funciona": "How it works",
      "Despesas": "Expenses",
      "Lançar despesa": "Add expense",
      "Descrição *": "Description *",
      "Categoria": "Category",
      "Valor (USD) *": "Amount (USD) *",
      "Salvar despesa": "Save expense",
      "Início": "Start",
      "Fim": "End",
      "Filtrar": "Filter",
      "Despesas lançadas": "Recorded expenses",
      "Nenhuma despesa encontrada.": "No expenses found.",
      "Zerar filtros": "Clear filters",
      "Mostrar pagas": "Show paid",
      "Mostrar abertas": "Show open",
      "Meus mapas": "My Maps",
      "Selecione o mapa do projeto em que você vai trabalhar.": "Select the project map you will work on.",
      "Clique em": "Click",
      "Abrir mapa": "Open map",
      "Nenhum mapa encontrado para a sua empresa": "No maps found for your company",
      "Projeto": "Project",
      "Ações": "Actions",
      "Sem projeto vinculado": "No linked project",
      "Configurações": "Settings",
      "Backup do banco de dados": "Database backup",
      "Baixar backup (.db)": "Download backup (.db)",
      "Importante": "Important",
      "Empresas & fusões inclusas": "Companies & included splices",
      "Fusões inclusas por lançamento": "Included splices per entry",
      "Endereço para invoice (nome + endereço completos)": "Invoice address (full name + address)",
      "Salvar empresa": "Save company",
      "Empresas cadastradas": "Registered companies",
      "Fusões inclusas": "Included splices",
      "Nome da sua empresa": "Your company name",
      "Endereço completo": "Full address",
      "CNPJ / Tax ID (opcional)": "CNPJ / Tax ID (optional)",
      "Telefone": "Phone",
      "E-mail": "Email",
      "Chave da API do Geoapify": "Geoapify API key",
      "Cabeçalho padrão da lousa": "Default board header",
      "Salvar dados da minha empresa": "Save my company data",
      "Produção": "Production",
      "Lançar": "Entry",
      "Lançar (foto)": "Photo Entry",
      "Usuários": "Users",
      "Idioma": "Language",
      "Português": "Portuguese",
      "Sair": "Logout",
      "Escolha a empresa. O campo de map só mostra mapas cadastrados para ela.": "Choose the company. The map field only shows maps registered for it.",
      "As regras de fusões inclusas e preços vêm da tela de Configurações.": "Included splice and pricing rules come from Settings.",
      "Esse formulário lança apenas uma linha por vez, igual a uma linha da planilha.": "This form records one line at a time, like one spreadsheet row.",
      "Todos os lançamentos aparecem depois na tela principal de Produção.": "All entries appear later on the main Production screen.",
      "Se você for usuário normal, só vê e lança os seus próprios registros. O admin vê tudo.": "If you are a regular user, you only see and enter your own records. The admin sees everything.",
    },
    pt: {}
  };

  function translateString(str, lang) {
    if (!str) return str;
    const map = textMap[lang] || {};
    return map[str.trim()] || str;
  }

  function translateTextNodes(lang) {
    if (lang !== 'en') return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    const toChange = [];
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!parent) continue;
      if (["SCRIPT","STYLE","CODE"].includes(parent.tagName)) continue;
      const raw = node.nodeValue;
      if (!raw || !raw.trim()) continue;
      const trimmed = raw.trim();
      if ((textMap.en[trimmed] || "").length) {
        toChange.push([node, raw.replace(trimmed, textMap.en[trimmed])]);
      }
    }
    toChange.forEach(([n,v]) => n.nodeValue = v);
  }

  function translateAttributes(lang) {
    document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(el => {
      const ph = el.getAttribute('placeholder') || '';
      const newPh = translateString(ph, lang);
      if (newPh !== ph) el.setAttribute('placeholder', newPh);
    });
    const title = document.title || '';
    const newTitle = translateString(title, lang);
    if (newTitle !== title) document.title = newTitle;
    document.documentElement.lang = lang === 'en' ? 'en' : 'pt-br';
  }

  function applyTranslations() {
    const lang = localStorage.getItem("lang") || "pt";
    const current = dict[lang] || dict.pt;
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.getAttribute("data-i18n");
      if (current[key]) el.textContent = current[key];
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (current[key]) el.setAttribute("placeholder", current[key]);
    });
    translateTextNodes(lang);
    translateAttributes(lang);
    const sel = document.getElementById("lang-switcher");
    if (sel) sel.value = lang;
  }

  window.setUILanguage = function(lang){
    localStorage.setItem("lang", lang || "pt");
    applyTranslations();
    document.dispatchEvent(new CustomEvent("ui-language-changed", {detail:{lang}}));
  };

  document.addEventListener("DOMContentLoaded", applyTranslations);
})();
