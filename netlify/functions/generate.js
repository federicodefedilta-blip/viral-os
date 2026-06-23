exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method Not Allowed' };
  }

  const GROQ_API_KEY = process.env.GROQ_API_KEY;
  if (!GROQ_API_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: 'API key non configurata' }) };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Body non valido' }) };
  }

  const { platform, lang, affCat, duration } = body;

  const prompt = `Sei il miglior creatore di contenuti virali su ${platform} in ${lang}. Il tuo obiettivo è creare script che esplodono in viralità nelle prime 24 ore e convertono in vendite affiliate.

DATA ATTUALE: ${new Date().toLocaleDateString('it-IT', {month:'long', year:'numeric'})}
PIATTAFORMA: ${platform}
LINGUA: ${lang}
CATEGORIA: ${affCat === 'auto' ? 'scegli la nicchia con il ROI più alto e la crescita più esplosiva in questo momento' : affCat}
DURATA: ${duration} secondi

REGOLE SCRIPT VIRALE OBBLIGATORIE:
1. Hook nei primi 2 secondi: deve essere uno shock, una domanda impossibile o una affermazione controversa che impedisce lo scroll
2. Loop psicologico: crea curiosità che si risolve solo alla fine (mai rivelare subito la risposta)
3. Ritmo veloce: frasi corte, max 8 parole per frase, niente pause
4. CTA affiliate NATURALE: integrata come consiglio personale, non come pubblicità
5. Finale con cliffhanger o call-to-action urgente ("solo per oggi", "prima che sparisca", ecc.)

ESEMPI DI HOOK FORTI:
- "Il 99% delle persone non sa che..."
- "Ho guadagnato X€ in 3 giorni con questo trucco"
- "Questo [prodotto] è illegale in 3 paesi europei. Ecco perché"
- "Ho smesso di [cosa comune] per 30 giorni. Risultato assurdo"

Rispondi SOLO con un oggetto JSON valido, senza markdown, senza backtick, senza testo prima o dopo.

{
  "nicchia": "nome nicchia specifica e di tendenza",
  "viral_score": numero da 1 a 100,
  "viral_score_reason": "perché questa nicchia esplode ora, dati concreti",
  "competitor_analysis": "chi domina questa nicchia ora e il loro punto debole che possiamo sfruttare",
  "trend_tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "affiliate": {
    "prodotto": "nome prodotto specifico con brand reale",
    "piattaforma": "Amazon/Hotmart/Clickbank/Awin/etc",
    "commissione": "X%",
    "perche": "perché converte: problema che risolve + urgenza + prova sociale",
    "cta": "frase CTA naturalissima da integrare nel video, sembra un consiglio non una pubblicità"
  },
  "script": "SCRIPT COMPLETO in ${lang} per ${duration} secondi. Inizia con hook devastante. Usa frasi cortissime. Ritmo veloce. Integra CTA affiliate come consiglio personale. Chiudi con urgenza o cliffhanger. MAX ${Math.round(parseInt(duration) * 2.5)} parole totali.",
  "titolo": "titolo clickbait SEO per ${platform}, max 60 caratteri, deve generare curiosità o shock",
  "descrizione": "descrizione con keyword SEO + CTA affiliate + urgenza, max 200 caratteri",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6", "#tag7", "#tag8", "#tag9", "#tag10"],
  "orario_pubblicazione": "orario ottimale per audience italiana su ${platform} (es: 19:00)",
  "tip_viralita": "un solo consiglio ultra-specifico e actionable per questa nicchia su ${platform} che pochi creators conoscono"
}`;

  try {
    const response = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${GROQ_API_KEY}`
      },
      body: JSON.stringify({
        model: 'llama-3.3-70b-versatile',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.8,
        max_tokens: 2000
      })
    });

    const data = await response.json();

    if (!response.ok) {
      return {
        statusCode: response.status,
        body: JSON.stringify({ error: data.error?.message || 'Errore Groq API' })
      };
    }

    const text = data.choices?.[0]?.message?.content || '';
    let clean = text.replace(/```json|```/g, '').trim();
    const start = clean.indexOf('{');
    const end = clean.lastIndexOf('}');
    if (start !== -1 && end !== -1) clean = clean.slice(start, end + 1);

    const parsed = JSON.parse(clean);

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed)
    };

  } catch (err) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: err.message })
    };
  }
};
