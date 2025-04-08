document.addEventListener('DOMContentLoaded', async () => {
    const forumID = document.querySelector('meta[name="forumID"]').getAttribute('value');
    const auth = document.querySelector('meta[name="auth"]').getAttribute('value');
    const subButton = document.getElementById('sub-btn');
    const postButton = document.getElementById('create-post-btn');
    let isSubscribed = subButton.dataset.subscribed === 'True'

    if (auth === 'True') {
        subButton.addEventListener('click', toggleSubscribe);
        postButton.addEventListener('click', createPostMenu);
    } else {
        subButton.addEventListener('click', () => {
            alert('Please log in to subscribe to this forum.');
        });
        postButton.addEventListener('click', () => {
            alert('Please log in to post in this forum.');
        });
    }
    async function toggleSubscribe() {
        const endpoint = isSubscribed
            ? `/forums/${forumID}/unsubscribe`
            : `/forums/${forumID}/subscribe`;
    
        try {
            const response = await fetch(endpoint, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
    
            if (response.status === 204) {
                console.log('No action taken (already subscribed/unsubscribed)');
                if (isSubscribed) {
                    subButton.innerText = 'Subscribe';
                    isSubscribed = !isSubscribed
                } else {
                    subButton.innerText = 'Unsubscribe';
                    isSubscribed = !isSubscribed

                }
                return;
            }
    
            if (response.ok) {
                const msg = await response.json();
                console.log(msg.message);
    
                // toggle state
                if (isSubscribed) {
                    subButton.innerText = 'Subscribe';
                    isSubscribed = !isSubscribed
                } else {
                    subButton.innerText = 'Unsubscribe';
                    isSubscribed = !isSubscribed
                }
            } else {
                const error = await response.json();
                alert(`Error: ${error.message || 'Unexpected error'}`);
            }
        } catch (err) {
            console.error('Request failed:', err);
            alert('Failed to toggle subscription. Please try again later.');
        }
    }
    
    async function createPostMenu() {
        console.log("Open post creation UI");
    
        // Create backdrop
        const backdrop = document.createElement('div');
        backdrop.id = 'post-backdrop';
        backdrop.style = `
            position: fixed;
            top: 0;
            left: 0;
            height: 100vh;
            width: 100vw;
            background: rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(4px);
            z-index: 9998;
            opacity: 0;
            transition: opacity 0.3s ease;
        `;
    
        // Create modal
        const modal = document.createElement('div');
        modal.id = 'post-modal';
        modal.style = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #121212;
            color: white;
            padding: 30px;
            border-radius: 12px;
            width: 450px;
            z-index: 9999;
            opacity: 0;
            transition: opacity 0.3s ease;
            box-shadow: 0 0 30px rgba(0,0,0,0.8);
        `;
        modal.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h3 style="margin: 0;">Create a Post</h3>
                <button id="close-modal" style="background: none; border: none; color: white; font-size: 20px; cursor: pointer;">&times;</button>
            </div>
            <input id="post-title" placeholder="Title" style="width: 100%; margin-bottom: 12px; padding: 10px; background: #1e1e1e; border: 1px solid #444; color: white; border-radius: 6px;" />
            <textarea id="post-body" placeholder="What's on your mind?" style="width: 100%; height: 120px; margin-bottom: 12px; padding: 10px; background: #1e1e1e; border: 1px solid #444; color: white; border-radius: 6px; resize: vertical;"></textarea>
            <button id="submit-post" style="width: 100%; padding: 10px; background: #4caf50; color: white; border: none; border-radius: 6px; cursor: pointer;">Post</button>
        `;
    
        document.body.appendChild(backdrop);
        document.body.appendChild(modal);
    
        requestAnimationFrame(() => {
            backdrop.style.opacity = 1;
            modal.style.opacity = 1;
        });
    
        document.getElementById('close-modal').addEventListener('click', () => {
            modal.style.opacity = 0;
            backdrop.style.opacity = 0;
            setTimeout(() => {
                modal.remove();
                backdrop.remove();
            }, 300);
        });
    
        document.getElementById('submit-post').addEventListener('click', async () => {
            const title = document.getElementById('post-title').value.trim();
            const body = document.getElementById('post-body').value.trim();
    
            if (!title || !body) {
                alert("All fields are required!");
                return;
            }
    
            try {
                const response = await fetch('/posts/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${auth}`
                    },
                    body: JSON.stringify({ 'forum':forumID, title:title, body:body })
                });
    
                if (response.ok) {
                    const data = await response.json();
                    alert(data.message);
                    modal.style.opacity = 0;
                    backdrop.style.opacity = 0;
                    setTimeout(() => {
                        modal.remove();
                        backdrop.remove();
                    }, 300);
                } else {
                    const err = await response.json();
                    alert(err.message || "Failed to create post.");
                }
            } catch (error) {
                alert("Something went wrong.");
                console.error(error);
            }
        });
    }
    
    
});
