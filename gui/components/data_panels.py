import tkinter as tk                                                                                                                                                                      
from tkinter import ttk                                                                                                                                                                   
from typing import List, Dict, Tuple                                                                                                                                                      
                                                                                                                                                                                          
class BaseDataPanel(ttk.Frame):                                                                                                                                                           
    """Base class for data display panels"""                                                                                                                                              
    def __init__(self, parent, columns: List[Tuple[str, str, int]]):                                                                                                                      
        """                                                                                                                                                                               
        :param columns: (internal_name, display_name, width_percentage)                                                                                                                   
        """                                                                                                                                                                               
        super().__init__(parent)                                                                                                                                                          
        self.columns = columns                                                                                                                                                            
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show='headings')                                                                                                  
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)                                                                                                     
                                                                                                                                                                                          
        # Configure tree columns                                                                                                                                                          
        for col_id, col_name, _ in columns:                                                                                                                                               
            self.tree.heading(col_id, text=col_name)                                                                                                                                      
                                                                                                                                                                                          
        self.tree.configure(yscrollcommand=self.scroll.set)                                                                                                                               
                                                                                                                                                                                          
        # Grid layout                                                                                                                                                                     
        self.tree.grid(row=0, column=0, sticky='nsew')                                                                                                                                    
        self.scroll.grid(row=0, column=1, sticky='ns')                                                                                                                                    
                                                                                                                                                                                          
        # Responsive configuration                                                                                                                                                        
        self.grid_columnconfigure(0, weight=1)                                                                                                                                            
        self.grid_rowconfigure(0, weight=1)                                                                                                                                               
                                                                                                                                                                                          
                                                                                                                                                                                          
    def update_data(self, items: List[Dict]):                                                                                                                                             
        """Update panel with new data (to be overridden)"""                                                                                                                               
        raise NotImplementedError                                                                                                                                                         
                                                                                                                                                                                          
class OrdersPanel(BaseDataPanel):                                                                                                                                                         
    """Replacement for original GUI_Orders"""                                                                                                                                             
    COLUMNS = [                                                                                                                                                                           
        ('pair', 'Pair', 25),                                                                                                                                                             
        ('status', 'Status', 25),                                                                                                                                                         
        ('side', 'Side', 20),                                                                                                                                                             
        ('flag', 'Flag', 10),                                                                                                                                                             
        ('variation', 'Variation', 20)                                                                                                                                                    
    ]                                                                                                                                                                                     
                                                                                                                                                                                          
    def __init__(self, parent):                                                                                                                                                           
        super().__init__(parent, self.COLUMNS)                                                                                                                                            
        # Set initial height to 8 rows
        self.tree.configure(height=8)
                                                                                                                                                                                          
    def update_data(self, orders: List[Dict]):                                                                                                                                            
        self.tree.delete(*self.tree.get_children())                                                                                                                                       
        # Set treeview height to number of rows (max 10)
        new_height = min(len(orders), 10) if len(orders) > 0 else 1
        self.tree.configure(height=new_height)
        
        for order in orders:                                                                                                                                                              
            self.tree.insert('', 'end', values=(                                                                                                                                          
                order['pair'],                                                                                                                                                            
                order.get('status', 'None'),                                                                                                                                           
                order.get('side', 'None'),                                                                                                                                                 
                order.get('flag', 'X'),                                                                                                                                                 
                order.get('variation', 'None')                                                                                                                                       
            ))                                                                                                                                                                            
                                                                                                                                                                                          
class BalancesPanel(BaseDataPanel):                                                                                                                                                       
    """Replacement for original GUI_Balances"""                                                                                                                                           
    COLUMNS = [                                                                                                                                                                           
        ('coin', 'Coin', 25),                                                                                                                                                             
        ('usd_price', 'USD Price', 20),                                                                                                                                                   
        ('total', 'Total', 20),                                                                                                                                                           
        ('free', 'Free', 20),                                                                                                                                                             
        ('total_usd', 'Total USD', 15)                                                                                                                                                    
    ]                                                                                                                                                                                     
                                                                                                                                                                                          
    def __init__(self, parent):                                                                                                                                                           
        super().__init__(parent, self.COLUMNS)
        # Set initial height to 8 rows
        self.tree.configure(height=8)                                                                                                                                            
                                                                                                                                                                                          
    def update_data(self, balances: List[Dict]):                                                                                                                                          
        self.tree.delete(*self.tree.get_children())
        # Set treeview height to number of rows (max 10)
        new_height = min(len(balances), 10) if len(balances) > 0 else 1
        self.tree.configure(height=new_height)
        
        for balance in balances:                                                                                                                                                          
            self.tree.insert('', 'end', values=(                                                                                                                                          
                balance['symbol'],                                                                                                                                                        
                f"${balance['usd_price']:.3f}",                                                                                                                                           
                f"{balance['total']:.4f}",                                                                                                                                                
                f"{balance['free']:.4f}",                                                                                                                                                 
                f"${balance['total'] * balance['usd_price']:.2f}"                                                                                                                         
            ))                                                                                                                                                                            
