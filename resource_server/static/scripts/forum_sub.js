document.addEventListener('DOMContentLoaded', () => {
    const forumID = document.querySelector('meta[name="forumID"').getAttribute('value');
    const subBtn = document.getElementById('sub-btn');
    const forumSubs = document.getElementById('forum-subs');
    let updateCount = true;
    if(!forumSubs || forumSubs === undefined){
        updateCount = false;    
    }

    subsVal = forumSubs.innerText.trim().replace('.', '').replace(',', '');
    if(isNaN(subsVal)){
        updateCount = false;
    }
    else{
        subsVal = parseInt(subsVal);
    }

    window.dependencyReady?.then(async () => {
        if (!localStorage.getItem('login')) {
            subBtn.innerText = 'Login to subscribe to this anime!';
            subBtn.addEventListener('click', () => {
                window.location.href = '/login';
            });
            return;
        }
        let isSubscribed = subBtn.getAttribute('subscribed').toLowerCase().trim() === 'true';
        subBtn.innerText = isSubscribed ? 'Unsubscribe' : 'Subscibe';

        subBtn.addEventListener('click', async () => {
            try {
                const response = await fetch(`/forums/${forumID}/${isSubscribed ? 'unsubscribe' : 'subscribe'}`, {
                        method: 'PATCH',
                        credentials:'include',
                        headers : {
                            'Content-Type' : 'application/json'
                        }
                    });

                    if(!response.ok){
                        throw new Error();
                    }

                    if(isSubscribed){
                        subBtn.innerText = 'Subscribe';
                        if(updateCount){
                            forumSubs.innerText = --subsVal;
                        }
                    }
                    else{
                        subBtn.innerText = 'Unsubscribe';
                        if(updateCount){
                            forumSubs.innerText = ++subsVal;
                        }
                    }

                    isSubscribed = !isSubscribed
            }
            catch (error) {
                console.error(error);
            }
        })
    });
});