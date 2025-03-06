from typing import Literal
import orjson
from redis import Redis
from redis.typing import ResponseT
import os

class RedisManager:
    def __init__(self, host : str, port : int, db : int = 0, password : str | None = None, redisAddtionalKwargs : dict = {}, configFilepath : os.PathLike = "/", configMappingTemplate : set = {'require', 'consumers'}) -> None:
        '''Instantiate a Redis manager class'''
        try:
            self.interface = Redis(host, port, db, password, **redisAddtionalKwargs)
        except Exception as e:
            print("Fatal error: Failed to connect to Redis instance")
            print(e)
        self.consumerGroups: set[str] = {}

        if not os.path.isfile(configFilepath):
            raise FileNotFoundError
        
        with open(configFilepath) as configFile:
            self._configData : dict = orjson.loads(configFile.read())['redis']
            
        self.CONFIG_TENPLATE : frozenset = frozenset(configMappingTemplate) 
        self._configFile : os.PathLike = configFilepath             # Save for later writes to config.json

    @property
    def configData(self) -> dict:
        return self._configData

    @configData.setter
    def configData(self, newConfigDict : dict) -> None:
        '''Set Redis configuration data for this instance'''
        if not isinstance(newConfigDict, dict):
            raise ValueError("Invalid value (!dict) passed to setter, rejected!")

        expected: set = self.CONFIG_TEMPLATE 
        given: set = set(newConfigDict.keys())
        errorList: list = []
        if given - expected:
            errorList.append("Required key(s) missing in new configuration dictionary")

        if expected - given:
            errorList.append("Additional keys present in new configuration dictionary")

        if errorList:
            errorList.append(f"Expected params: {', '.join(expected)}")
            raise ValueError("\n".join(errorList))
        
        self._configData = newConfigDict

    def updateRedisConfigurations(self) -> None:
        with open(self.CONFIG_FILE, 'rb+') as configFileBuffer:
            _configData : dict = orjson.loads(configFileBuffer.read())
            _configData['redis'] = self.configData

            configFileBuffer.write(orjson.dumps(_configData))
        
            

    # def updateGroups(self, *args) -> None:
    #     '''Make consumer groups for different db operations, as specified in config file under `database` section'''
    #     for consumer in self.consumerGroups:
    #         self.interface.xinfo_groups
    #     ...