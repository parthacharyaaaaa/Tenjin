# Tenjin
A secure, event-driven backend system built on Flask, Redis, and Postgres — with dual-server architecture, custom background workers, and JWT-based authentication baked in.
## Overview
This project is composed of 2 codependent Flask servers: The resource server (often abbreviated in code as `RS`) and the auth server. Both of these servers are contained in their own directories with dedicated README files containing their respective API documentations, overviews, and any other relevant aspects I found worth explaining explicitly.

## Installation Guide:
Requirements:
* Python 3.10.X
* Redis server 7.4.X
* psql 15.X

Once all requirements are satisfied, set up a virtual environment, navigate to the root directory if not already there, and run the following:
```bash
$ pip install -r requirements.txt
```
Perfect, now we have all Python dependencies ready to support our application.

### Environment Variables
In the test deployment, without a container loading env variables, we would also need to load environment variables through Python itself. For both servers, this process is confined to only 1 file, i.e. the Flask config file.
Note that for independent scripts (see `/resource_server/scripts`) the environment variables are loaded by each and every one of them. Both servers are equipped with an example .env file as `.env.example` for clarity.
The app factory in both servers would stop the app from running if any mandatory environment variable was not found or misconfigured (type/value mismatch).

### ACL
Both the auth server and the resource server are meant to have strict access control logic, for both Redis as well as Postgres. The actual ACL configuration can be seen in the `authz` directory, present in both the server packages
(The password fields have of course been omitted). The passwords are read from the environment at runtime and used to connect to the Redis client.

The same applies for Postgres. Although there is a super user provision for operations like `flask_sqlalchemy.SQLAchemy.create_all()`, the actual runtime would use a separate worker user with only the priviliges the server would need (CRUD, as well asa few extra such as connection priviliges)

## Running the application
Now that all dependencies have been resolved and ACL has been set up, we can actually run the servers. For test deployments, the flow is as follows:

### 1) Ensure Postgres is running properly as a service
### 2) Spin up Redis servers
The entire application would require 3 Redis instances (1 for RS, 2 for Auth). The ports are specified under the `config` directory in both server packages. To actually ensure that the servers run with the proper ACL settings, make sure to update the redis.conf config file (Typically under `/etc/redis` to include the ACL filepath (This would require superuser access since this directory is priviliged).
The actual command would then look like:
```bash
$ sudo redis-server /etc/redis/file.conf
```
This would start the resource server's Redis server on the default TCP port 6379 (if you have changed the config file, please use the `--port` argument as well)
Now, to start up the auth server's Redis servers
```bash
$ sudo redis-server /etc/redis/authfile.conf --port 6100
```
```bash
$ sudo redis-server /etc/redis/authfile.conf --port 6200
```
This would start both the token store as well as the synced store used by the auth server.
### 3) Starting background scripts
Under `resource_server/scripts` exist a few Python scripts that act as background workers that consume events as batches and flush their effects into the database with a single network call. You can see the resource server's README for more details about this architecture.
These scripts are meant to be run as Python modules using the `-m` flag, since they make use of absolute imports.
**Example:**
```bash
$ python -m resource_server.scripts.batch_workers.counter
```
This would start the batch worker responsible for periodically flushing distributed counters from Redis to Postgres.

### 4) Starting the servers:
Now that all background scripts are active, Redis servers are running, and Postgres service is running, we can finally start the Flask servers. For testing/development I have included a file to run both servers. From the root directory:<br>
i) Resource server:
  ```bash
  $ python ./run.py
  ```

ii) Auth server:
  ```bash
  $ python ./auth_run.py
  ```
### Authentication flow:
After populating the database via `genesis.py`, you will have access to the TENJIN superuser account based on the password entered in the .env file. However, it also possible to create a new account just as easily.

![Authentication Flow](/auth_flow.png)

**To see detailed usage, please refer to the respective servers' README files.**

## Possible improvements
As much as I would love to talk about what this project does right, there are still a few improvements that could possibly be added, such as:
1) Containerization
2) Shifting to an asynchronous framework like Quart, considering the amount of I/O interacitivity in all endpoints (Postgres, Redis, network calls involved in auth flows)
3) Inter-server security: While the auth and resource servers currently use a shared API key for mutual communication, introducing a more robust mechanism like mTLS or signed request tokens would further strengthen internal security.
4) Adding testing modules

## Acknowledegments
Although this was an individual project, calling it a “solo mission” wouldn’t quite do justice to the invisible army of tools, docs, and open-source magic that made it possible.

A few shout-outs are in order:
* The Flask community: For crafting a micro-framework that never feels small. The simplicity of its core, the richness of its ecosystem, and the generosity of its community have made this one of the best dev experiences I’ve had.
* Redis: Blazingly fast, beautifully minimal, and just always there when you need it.
* PostgreSQL – For being a rock-solid database I could trust to hold things together no matter what experimentation I was up to.
* My aging laptop — thank you for not catching fire while running 3 Redis instances, Postgres, background scripts, and two Flask servers at once. You did good, old friend.
And lastly, to the endless world of open-source libraries, blog posts, GitHub issues, and kind (and sometimes condescending) strangers on Stack Overflow — thank you. You helped me stay focused on the bigger picture without reinventing every wheel.
