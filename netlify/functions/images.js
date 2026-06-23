exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method Not Allowed' };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Body non valido' }) };
  }

  const { prompts } = body;

  try {
    const imagePromises = prompts.map(async (prompt, i) => {
      const encoded = encodeURIComponent(`${prompt}, vertical 9:16, cinematic, vibrant, sharp`);
      const url = `https://image.pollinations.ai/prompt/${encoded}?width=576&height=1024&nologo=true&seed=${Date.now() + i}`;
      
      const response = await fetch(url, {
        headers: { 'User-Agent': 'Viral-OS/1.0' }
      });

      if (!response.ok) throw new Error(`Immagine ${i+1} fallita: ${response.status}`);

      const buffer = await response.arrayBuffer();
      return Buffer.from(buffer).toString('base64');
    });

    // Generate sequentially to avoid timeout
    const images = [];
    for (const promise of imagePromises) {
      images.push(await promise);
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ images })
    };

  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message }) };
  }
};
