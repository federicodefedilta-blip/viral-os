exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method Not Allowed' };
  }

  const HF_API_KEY = process.env.HUGGINGFACE_API_KEY;
  if (!HF_API_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: 'Hugging Face API key non configurata' }) };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Body non valido' }) };
  }

  const { prompts } = body; // array of 4 prompts

  try {
    const imagePromises = prompts.map(async (prompt) => {
      const response = await fetch(
        'https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0',
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${HF_API_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            inputs: `${prompt}, vertical 9:16, cinematic, highly detailed, vibrant colors, sharp focus`,
            parameters: {
              width: 576,
              height: 1024,
              num_inference_steps: 20,
              guidance_scale: 7.5
            }
          })
        }
      );

      if (!response.ok) {
        throw new Error(`Errore immagine: ${response.status}`);
      }

      const imageBuffer = await response.arrayBuffer();
      return Buffer.from(imageBuffer).toString('base64');
    });

    const images = await Promise.all(imagePromises);

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ images })
    };

  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message }) };
  }
};
