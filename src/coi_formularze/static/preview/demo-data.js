// Demo Data: made-up Dictionaries and a made-up citizen standing in for the real
// registries. Anything not covered here is shown as a placeholder naming the source.

const fold = (s) => String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[\s_-]/g, "");

const DICTIONARIES = {
  Kraje: ["Polska", "Niemcy", "Czechy", "Słowacja", "Litwa", "Ukraina", "Wielka Brytania", "Francja", "Hiszpania", "Włochy"],
  Województwo: [
    "dolnośląskie", "kujawsko-pomorskie", "lubelskie", "lubuskie", "łódzkie", "małopolskie",
    "mazowieckie", "opolskie", "podkarpackie", "podlaskie", "pomorskie", "śląskie",
    "świętokrzyskie", "warmińsko-mazurskie", "wielkopolskie", "zachodniopomorskie",
  ],
  Powiat: ["elbląski", "lubelski", "radzyński", "warszawa", "krakowski", "bydgoski"],
  Gmina: ["Elbląg", "Ulan-Majorat", "Warszawa", "Kraków", "Bydgoszcz", "Lublin"],
  Miejscowość: ["Elbląg", "Ulan-Majorat", "Warszawa", "Kraków", "Bydgoszcz", "Lublin", "Nowakowo"],
  "Numery kierunkowe": ["+48", "+49", "+420", "+421", "+370", "+380", "+44"],
};
const DICT_BY_KEY = new Map(Object.entries(DICTIONARIES).map(([k, v]) => [fold(k), v]));

const ADDRESS = {
  Voivodeship: "mazowieckie", District: "warszawa", Community: "Warszawa", Place: "Warszawa",
  StreetType: "ul.", Street: "Polna", HouseNumber: "1", ApartmentNumber: "2", PostalCode: "00-001",
};

const SOURCES = {
  MLPZ: {
    name: "JAN", secondName: "", surname: "NOWAK", pesel: "44051401359",
    phoneNumber: "+48123456789", email: "jan.nowak@mail.pl", country: "Polska",
    voivodeship: "mazowieckie", district: "warszawa", community: "Warszawa", place: "Warszawa",
    postCode: "00-001", street: "Polna", building: "1", apartment: "2",
    institutionName: "", descriptiveOrganizationName: "",
  },
  PESEL: {
    pesel: "44051401359", name: "JAN", surname: "NOWAK", birthDate: "1944-05-14",
    citizenship: "polskie", motherName: "ANNA", birthPlace: "Warszawa", motherSurname: "KOWALSKA",
    sex: "mężczyzna", fatherName: "PIOTR", secondName: "", birthCountry: "Polska",
    foreignBirthUSC: "", familyName: "NOWAK", fatherSurname: "NOWAK", maritalStatus: "żonaty",
  },
  PESEL_ADDRESS: Object.fromEntries(
    ["permanentAddress", "temporaryAddress"].flatMap((kind) =>
      Object.entries(ADDRESS).map(([k, v]) => [kind + k, v])),
  ),
};
const SOURCE_BY_KEY = new Map(
  Object.entries(SOURCES).map(([src, attrs]) => [src, new Map(Object.entries(attrs).map(([k, v]) => [fold(k), v]))]),
);

// Multi-level address tree (TERYT_DOWODOWY, TERYT_4G_DOWODOWY): województwo › powiat ›
// gmina › miejscowość. Only a few branches are filled in; the rest get one sample child.
const TREE_LEVELS = ["Województwo", "Powiat", "Gmina", "Miejscowość"];
const TERYT = {
  lubelskie: {
    radzyński: { "Ulan-Majorat": ["Ulan-Majorat", "Ulan Duży", "Kępki"], Radzyń: ["Radzyń Podlaski"] },
    lubelski: { Jastków: ["Jastków", "Panieńszczyzna"], Konopnica: ["Konopnica", "Motycz"] },
    "Lublin (miasto)": { Lublin: ["Lublin"] },
  },
  "warmińsko-mazurskie": {
    elbląski: { Elbląg: ["Nowakowo", "Przezmark", "Gronowo Górne"], Tolkmicko: ["Tolkmicko", "Kadyny"] },
    "Elbląg (miasto)": { Elbląg: ["Elbląg"] },
  },
  mazowieckie: { Warszawa: { Warszawa: ["Warszawa"] }, piaseczyński: { Piaseczno: ["Piaseczno", "Józefosław"] } },
  małopolskie: { Kraków: { Kraków: ["Kraków"] } },
  "kujawsko-pomorskie": { Bydgoszcz: { Bydgoszcz: ["Bydgoszcz"] } },
};
const SAMPLE_CHILD = ["(przykładowy powiat)", "(przykładowa gmina)", "(przykładowa miejscowość)"];

/** Tree dictionary access, or null when `name` isn't a known tree. */
export function tree(name) {
  if (!/^teryt/i.test(String(name ?? ""))) return null;
  return {
    levels: TREE_LEVELS,
    /** Options for the next level after `path` (an array of chosen names). */
    options(path) {
      if (path.length === 0) return DICTIONARIES.Województwo;
      let node = TERYT;
      for (const step of path) node = node && !Array.isArray(node) ? node[step] : null;
      if (Array.isArray(node)) return path.length < TREE_LEVELS.length ? node : [];
      if (node) return Object.keys(node);
      return path.length < TREE_LEVELS.length ? [SAMPLE_CHILD[path.length - 1]] : [];
    },
  };
}

/** Options of a Dictionary, or null when the Demo Data doesn't have it. */
export function dictionary(name) {
  return DICT_BY_KEY.get(fold(name)) ?? null;
}

/** Resolve "<SOURCE>.<attribute>" (nested attributes may use dots). */
export function initialData(ref) {
  const dot = String(ref).indexOf(".");
  if (dot < 0) return { known: false };
  const attrs = SOURCE_BY_KEY.get(ref.slice(0, dot));
  const key = fold(ref.slice(dot + 1).replace(/\./g, ""));
  if (!attrs || !attrs.has(key)) return { known: false };
  return { known: true, value: attrs.get(key) };
}

export const INITIAL_DATA_REF = /^(PESEL|PESEL_ADDRESS|MLPZ)\.\S+$/;
