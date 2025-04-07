document.addEventListener('DOMContentLoaded', () => {
    const userID = document.querySelector('meta[name="user"]').getAttribute('value');
    const userName = document.querySelector('meta[name="username"]').getAttribute('value');

    const cursors = {
        posts: '0',
        animes: '0',
        forums: '0'
    };

    const endpoints = {
        posts: `/users/profile/${userID}/posts`,
        animes: `/users/profile/${userID}/animes`,
        forums: `/users/profile/${userID}/forums`
    };

    async function fetchMore(section) {
        const url = `${endpoints[section]}?cursor=${cursors[section]}`;
        const res = await fetch(url);
        if (!res.ok) return;

        const data = await res.json();
        const items = data.items || data[section];
        const reachedEnd = data.end === true;
        const cursor = data.cursor;

        const container = document.getElementById(`${section}-cards`);
        const showMoreBtn = document.querySelector(`.show-more[data-type="${section}"]`);

        // If no items and it's the initial fetch
        if ((!items || items.length === 0) && container.children.length === 0) {
            const msg = document.createElement('p');
            msg.classList.add('empty-msg');
            msg.textContent = `${userName} does not have any ${section} so far.`;
            container.appendChild(msg);
            if (showMoreBtn) showMoreBtn.style.display = 'none';
            return;
        }

        if (items?.length > 0) {
            items.forEach(item => {
                const card = document.createElement('div');
                card.classList.add('profile-card-item');
            
                if (section === 'posts') {
                    card.innerHTML = `
                        <h4 class="card-title">${item.title}</h4>
                        <div class="card-meta">
                            <span><strong>Score:</strong> ${item.score}</span>
                            <span><strong>Comments:</strong> ${item.comments}</span>
                            <span><strong>Saved:</strong> ${item.saves}</span>
                            ${item.flair ? `<span class="flair">${item.flair}</span>` : ''}
                        </div>
                        <p class="card-snippet">${item.body.slice(0, 120)}${item.body.length > 120 ? '...' : ''}</p>
                        <div class="card-footer">
                            <span>${item.epoch}</span>
                            ${item.closed ? `<span class="closed-badge">Closed</span>` : ''}
                        </div>
                    `;
                    card.addEventListener('click', async () => {
                        sessionStorage.setItem('selectedForum', JSON.stringify(item));
                        window.location.href=`/view/post/${item.id}`;
                    })
                } else if (section === 'animes') {
                    card.innerHTML = `
                        <h4 class="card-title">${item.title}</h4>
                        <div class="card-meta">
                            <span><strong>Rating:</strong> ${item.rating}</span>
                            <span><strong>MAL Rank:</strong> #${item.mal_ranking}</span>
                            <span><strong>Members:</strong> ${item.members.toLocaleString()}</span>
                        </div>
                        <p class="card-snippet">${item.synopsis?.slice(0, 120) || 'No synopsis'}${item.synopsis?.length > 120 ? '...' : ''}</p>
                    `;
                    card.addEventListener('click', async () => {
                        sessionStorage.setItem('selectedAnime', JSON.stringify(item));
                        window.location.href=`/view/anime/${item.id}`;
                    })
                } else if (section === 'forums') {
                    card.innerHTML = `
                        <h4 class="card-title">${item.name}</h4>
                        <div class="card-meta">
                            <span><strong>Subscribers:</strong> ${item.subscribers}</span>
                            <span><strong>Posts:</strong> ${item.posts}</span>
                            <span><strong>Admins:</strong> ${item.admin_count}</span>
                        </div>
                        <p class="card-snippet">${item.description?.slice(0, 120) || 'No description'}${item.description?.length > 120 ? '...' : ''}</p>
                        <div class="card-footer">
                            <span>Created on ${item.epoch}</span>
                        </div>
                    `;
                    card.addEventListener('click', async () => {
                        sessionStorage.setItem('selectedForum', JSON.stringify(item));
                        window.location.href=`/view/forum/${item.name}`;
                    })
                }
            
                container.appendChild(card);
            });
            

            cursors[section] = cursor || cursors[section];
        }

        if (reachedEnd && showMoreBtn) {
            showMoreBtn.style.display = 'none';
        }
    }

    document.querySelectorAll('.show-more').forEach(btn => {
        btn.addEventListener('click', () => fetchMore(btn.dataset.type));
    });

    fetchMore('posts');
    fetchMore('animes');
    fetchMore('forums');
});
