// Favicon badge для непрочитанных сообщений в мессенджере.
// Рисует красный бейдж с числом поверх текущего favicon через canvas.
(function () {
    const origLink = document.querySelector('link[rel="icon"]');
    const origFaviconHref = origLink ? origLink.href : '/favicon.ico';
    const size = 32;
    const cache = {};

    function draw(count) {
        if (cache[count]) return Promise.resolve(cache[count]);
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            const canvas = document.createElement('canvas');
            canvas.width = size;
            canvas.height = size;
            const ctx = canvas.getContext('2d');
            img.onload = () => {
                ctx.clearRect(0, 0, size, size);
                ctx.drawImage(img, 0, 0, size, size);
                if (count > 0) {
                    ctx.fillStyle = '#ef4444';
                    ctx.beginPath();
                    ctx.arc(size - 9, 9, 9, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.fillStyle = '#fff';
                    ctx.font = 'bold 13px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(count > 9 ? '9+' : String(count), size - 9, 10);
                }
                try {
                    const url = canvas.toDataURL('image/png');
                    cache[count] = url;
                    resolve(url);
                } catch (e) {
                    // CORS tainting — возвращаем оригинал
                    resolve(origFaviconHref);
                }
            };
            img.onerror = () => resolve(origFaviconHref);
            img.src = origFaviconHref;
        });
    }

    window.setFaviconBadge = async function (count) {
        try {
            const url = await draw(count || 0);
            let link = document.querySelector('link[rel="icon"]');
            if (!link) {
                link = document.createElement('link');
                link.rel = 'icon';
                document.head.appendChild(link);
            }
            link.href = url;
        } catch (e) {
            // не критично
        }
    };
})();
