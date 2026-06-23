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

  const prompt = `Sei un esperto di content marketing virale e affiliate marketing. Analizza i trend attuali del ${new Date().toLocaleDateString('it-IT', {month:'long', year:'numeric'})} e crea una strategia completa per un video short.

Parametri:
- Piattaforma: ${platform}
- Lingua: ${lang}
- Categoria affiliate: ${affCat === 'auto' ? 'scegli tu la categoria con il ROI più alto in questo momento' : affCat}
- Durata video: ${duration} secondi

Rispondi SOLO con un oggetto JSON valido, senza markdown, senza backtick, senza testo prima o dopo.

{
  "nicchia": "nome della nicchia scelta",
  "viral_score": numero da 1 a 100,
  "viral_score_reason": "perché questa nicchia ha questo score in questo momento",
  "competitor_analysis": "breve analisi della competizione attuale su questa nicchia",
  "trend_tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "affiliate": {
    "prodotto": "nome del prodotto specifico consigliato",
    "piattaforma": "Amazon/Hotmart/Clickbank/Awin/etc",
    "commissione": "X%",
    "perche": "perché questo prodotto converte bene con questa nicchia ora",
    "cta": "frase call-to-action naturale da integrare nel video"
  },
  "script": "script completo del video in ${lang}, ottimizzato per ${platform}, con hook fortissimo nei primi 3 secondi e CTA affiliate integrata in modo naturale, durata ${duration} secondi",
  "titolo": "titolo ottimizzato SEO per ${platform}, max 60 caratteri",
  "descrizione": "descrizione ottimizzata con keyword SEO e CTA affiliate, max 200 caratteri",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6", "#tag7", "#tag8", "#tag9", "#tag10"],
  "orario_pubblicazione": "orario migliore per pubblicare per audience italiana (es: 18:30)",
  "tip_viralita": "un consiglio specifico e actionable per aumentare le chance di viralità su ${platform}"
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
