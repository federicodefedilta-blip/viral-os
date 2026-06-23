exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method Not Allowed' };
  }

  const PEXELS_API_KEY = process.env.PEXELS_API_KEY;
  if (!PEXELS_API_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: 'Pexels API key non configurata' }) };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Body non valido' }) };
  }

  const { prompts, nicchia } = body;

  // Extract search keywords from the nicchia or first prompt
  const keywords = (nicchia || prompts?.[0] || 'lifestyle viral').split(',')[0].trim();

  const queries = [
    keywords,
    `${keywords} cinematic`,
    `${keywords} dramatic`,
    `${keywords} lifestyle`
  ];

  try {
    const videoResults = [];

    for (const query of queries) {
      const res = await fetch(
        `https://api.pexels.com/videos/search?query=${encodeURIComponent(query)}&orientation=portrait&per_page=5`,
        { headers: { Authorization: PEXELS_API_KEY } }
      );

      if (!res.ok) throw new Error(`Pexels error: ${res.status}`);

      const data = await res.json();
      const video = data.videos?.[0];

      if (video) {
        // Pick smallest HD file to keep response fast
        const file = video.video_files
          .filter(f => f.quality === 'hd' || f.quality === 'sd')
          .sort((a, b) => a.width - b.width)[0];

        videoResults.push({
          url: file?.link || video.video_files[0].link,
          thumbnail: video.image,
          id: video.id,
          photographer: video.user.name,
          pexels_url: video.url
        });
      } else {
        // fallback: search generic if no result
        videoResults.push(null);
      }
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videos: videoResults, type: 'pexels' })
    };

  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message }) };
  }
};
