import pandas as pd
from py2neo import Graph
from datetime import datetime
import time, ast, json
from Firehose_v2.DfHelper import DfHelper
         
class Neo4jDataAccess:
    BATCH_SIZE=2000
    
    def __init__(self, debug=False): 
        self.creds = {}
        self.debug=debug
        self.tweetsandaccounts = """
                  UNWIND $tweets AS t
                      //Add the Tweet 
                    MERGE (tweet:Tweet {tweet_id:t.tweet_id})
                        ON CREATE SET 
                            tweet.text = t.text,
                            tweet.created_at = t.tweet_created_at,
                            tweet.favorite_count = t.favorite_count,
                            tweet.retweet_count = t.retweet_count,
                            tweet.record_created_at = timestamp(),
                            tweet.job_id = t.job_id,
                            tweet.hashtags = t.hashtags,
                            tweet.hydrated = t.hydrated,
                            tweet.type = t.tweet_type
                        ON MATCH SET 
                            tweet.text = t.text,
                            tweet.favorite_count = t.favorite_count,
                            tweet.retweet_count = t.retweet_count,
                            tweet.record_updated_at = timestamp(),
                            tweet.job_id = t.job_id,
                            tweet.hashtags = t.hashtags,
                            tweet.hydrated = t.hydrated,
                            tweet.type = t.tweet_type
                    
                    //Add Account
                    MERGE (user:Account {id:t.user_id})                    
                        ON CREATE SET 
                            user.id = t.user_id,
                            user.name = t.name,
                            user.screen_name = t.user_screen_name,
                            user.followers_count = t.user_followers_count,
                            user.friends_count = t.user_friends_count,
                            user.created_at = t.user_created_at,
                            user.record_created_at = timestamp(),
                            user.job_name = t.job_id
                        ON MATCH SET 
                            user.name = t.user_name,
                            user.screen_name = t.user_screen_name,
                            user.followers_count = t.user_followers_count,
                            user.friends_count = t.user_friends_count,
                            user.created_at = t.user_created_at,
                            user.record_updated_at = timestamp(),
                            user.job_name = t.job_id                                

                    //Add Reply to tweets if needed
                    FOREACH(ignoreMe IN CASE WHEN t.tweet_type='REPLY' THEN [1] ELSE [] END | 
                        MERGE (retweet:Tweet {tweet_id:t.reply_tweet_id})            
                            ON CREATE SET retweet.tweet_id=t.reply_tweet_id,
                            retweet.record_created_at = timestamp(),
                            retweet.job_id = t.job_id,
                            retweet.hydrated = 'PARTIAL'
                    )
                    
                    //Add QUOTE_RETWEET to tweets if needed
                    FOREACH(ignoreMe IN CASE WHEN t.tweet_type='QUOTE_RETWEET' THEN [1] ELSE [] END | 
                        MERGE (quoteTweet:Tweet {tweet_id:t.quoted_status_id})            
                            ON CREATE SET quoteTweet.tweet_id=t.quoted_status_id,
                            quoteTweet.record_created_at = timestamp(),
                            quoteTweet.job_id = t.job_id,
                            quoteTweet.hydrated = 'PARTIAL'
                    )
                    
                    //Add RETWEET to tweets if needed
                    FOREACH(ignoreMe IN CASE WHEN t.tweet_type='RETWEET' THEN [1] ELSE [] END | 
                        MERGE (retweet:Tweet {tweet_id:t.retweet_id})            
                            ON CREATE SET retweet.tweet_id=t.retweet_id,
                            retweet.record_created_at = timestamp(),
                            retweet.job_id = t.job_id,
                            retweet.hydrated = 'PARTIAL'
                    )
        """
        
        self.tweeted_rel = """UNWIND $tweets AS t
                    MATCH (user:Account {id:t.user_id})
                    MATCH (tweet:Tweet {tweet_id:t.tweetId})  
                    MATCH (tweet:Tweet {tweet_id:t.tweet_id})
                    MATCH (replied:Tweet {tweet_id:t.reply_tweet_id})     
                    MATCH (quoteTweet:Tweet {tweet_id:t.quoted_status_id})    
                    MATCH (retweet:Tweet {tweet_id:t.retweet_id})   
                    WITH user, tweet, replied, quoteTweet, retweet
                                        
                    MERGE (user)-[r:TWEETED]->(tweet)
                    
                    FOREACH(ignoreMe IN CASE WHEN tweet.tweet_type='REPLY' THEN [1] ELSE [] END | 
                        MERGE (tweet)-[:REPLYED]->(replied)
                    )
                    
                    FOREACH(ignoreMe IN CASE WHEN tweet.tweet_type='QUOTE_RETWEET' THEN [1] ELSE [] END | 
                        MERGE (tweet)-[:QUOTED]->(quoteTweet)
                    )
                    
                    FOREACH(ignoreMe IN CASE WHEN tweet.tweet_type='REPLY' THEN [1] ELSE [] END | 
                        MERGE (tweet)-[:RETWEETED]->(retweet)
                    )
                   
        """

    
    def __get_neo4j_creds(self, role_type):
        with open('neo4jcreds.json') as json_file:
            creds = json.load(json_file)
            res = list(filter(lambda c: c["type"]==role_type, creds))
            if len(res): 
                return res[0]["creds"]
            else: 
                return None
    
    def save_parquet_df_to_graph(self, df, job_name):
        pdf = DfHelper(self.debug).normalize_parquet_dataframe(df)
        if self.debug: print('Saving to Neo4j')
        self.__save_df_to_graph(pdf, job_name)
        
    # This saves the User and Tweet data right now
    def __save_df_to_graph(self, df, job_name):
        creds = self.__get_neo4j_creds('writer')        
        # connect to authenticated graph database
        self.graph = Graph(host=creds['host'], port=creds['port'], user=creds['user'], password=creds['password'])

        global_tic=time.perf_counter()      
        params = []
        tic=time.perf_counter()
        for index, row in df.iterrows():
            #determine the type of tweet
            tweet_type='TWEET'
            if row["in_reply_to_status_id"] is not None and row["in_reply_to_status_id"] >0:
                tweet_type="REPLY"
            elif row["quoted_status_id"] is not None and row["quoted_status_id"] >0:
                tweet_type="QUOTE_RETWEET"                
            elif row["retweet_id"] is not None and row["retweet_id"] >0:
                tweet_type="RETWEET"
            params.append({'tweet_id': row['status_id_str'], 
                       'text': row['full_text'],    
                       'tweet_created_at': row['created_at'].to_pydatetime(),  
                       'favorite_count': row['favorite_count'],        
                       'retweet_count': row['retweet_count'],         
                       'tweet_type': tweet_type,                                              
                       'job_id': job_name,                 
                       'user_id': row['user_id'],                            
                       'user_name': row['user_name'],                                             
                       'user_screen_name': row['user_screen_name'],                 
                       'user_followers_count': row['user_followers_count'],           
                       'user_friends_count': row['user_friends_count'],    
                       'user_created_at': pd.Timestamp(row['user_created_at'], unit='s').to_pydatetime(), 
                       'reply_tweet_id': row['in_reply_to_status_id'],    
                       'quoted_status_id': row['quoted_status_id'],    
                       'retweet_id': row['retweet_id'],    
                      })
            if index % self.BATCH_SIZE == 0 and index>0:
                self.__write_to_neo(params)
                toc=time.perf_counter()
                if self.debug: print(f"Neo4j Periodic Save Complete in  {toc - tic:0.4f} seconds")
                params = []
                tic=time.perf_counter()
        
        self.__write_to_neo(params)
        toc=time.perf_counter()
        print(f"Neo4j Import Complete in  {toc - global_tic:0.4f} seconds")
        
    def __write_to_neo(self, params):
        try: 
            tx = self.graph.begin(autocommit=False)
            tx.run(self.tweetsandaccounts, tweets = params)
            tx.run(self.tweeted_rel, tweets = params)            
            tx.commit()
        except Exception as inst:
            print(type(inst))    # the exception instance
            print(inst.args)     # arguments stored in .args
            print(inst)          # __str__ allows args to be printed directly,