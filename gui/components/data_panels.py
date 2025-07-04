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

        # Configure zebra striping
        self.tree.tag_configure('evenrow', background='#333333')
        self.tree.tag_configure('oddrow', background='#404040')
                                                                                                                                                                                          
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
        # Set initial height to 5 rows                                                                                                                                                    
        self.tree.configure(height=5)                                                                                                                                                            
                                                                                                                                                                                          
    def update_data(self, orders: List[Dict]):
        self.tree.delete(*self.tree.get_children())
        # Set height between 5-15 rows based on actual data count                                                                                                                         
        display_height = max(min(len(orders), 15), 5)                                                                                                                                     

        # But the Treeview shows the entire dataset regardless of visible height                                                                                                          
        for i, order in enumerate(orders):                                                                                                                                                
            self.tree.insert('', 'end', values=(                                                                                                                                          
                order['pair'],                                                                                                                                                            
                order.get('status', 'None'),                                                                                                                                              
                order.get('side', 'None'),                                                                                                                                                
                order.get('flag', 'X'),                                                                                                                                                   
                order.get('variation', 'None')                                                                                                                                            
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))                                                                                                                             

        # Set the height AFTER inserting all items so the view adapts to visible height                                                                                                   
        self.tree.configure(height=display_height)                                                                                                                                        
                                                                                                                                                                                          
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
        # Set initial height to 5 rows
        self.tree.configure(height=5)

    def update_data(self, balances: List[Dict]):
        self.tree.delete(*self.tree.get_children())
        # Set height between 5-15 rows based on actual data count
        display_height = max(min(len(balances), 15), 5)

        # But the Treeview shows the entire dataset regardless of visible height
        for i, balance in enumerate(balances):
            self.tree.insert('', 'end', values=(
                balance['symbol'],
                f"${balance['usd_price']:.3f}",
                f"{balance['total']:.4f}",
                f"{balance['free']:.4f}",
                f"${balance['total'] * balance['usd_price']:.2f}"
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))

        # Set the height AFTER inserting all items so the view adapts to visible height
        self.tree.configure(height=display_height)
